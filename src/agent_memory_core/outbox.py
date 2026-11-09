"""Reliable PostgreSQL outbox delivery with bounded retry and dead-letter recovery.

Database-local handlers must use the supplied connection and must not commit:
their effects and the acknowledgement share one PostgreSQL transaction.
External effects remain at-least-once and require destination-side idempotency.
"""
from __future__ import annotations

import json


class OutboxWorker:
    def __init__(self, conn, *, max_attempts: int = 8,
                 base_retry_seconds: float = 2.0,
                 max_retry_seconds: float = 300.0):
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if base_retry_seconds <= 0 or max_retry_seconds < base_retry_seconds:
            raise ValueError("Invalid retry interval bounds")
        self.conn = conn
        self.max_attempts = max_attempts
        self.base_retry_seconds = base_retry_seconds
        self.max_retry_seconds = max_retry_seconds

    def consume(self, *, event_type: str, handler, limit: int = 20) -> int:
        """Consume up to the requested limit; return successful acknowledgements.

        SKIP LOCKED permits concurrent workers. Handler writes and acknowledgement
        commit together. Failures roll back handler writes and are rescheduled
        separately with bounded exponential backoff.
        """
        if not event_type or not (1 <= limit <= 100):
            raise ValueError("Event type and limit required")
        done = 0
        for _ in range(limit):
            event_id = None
            attempts = 0
            try:
                with self.conn.transaction():
                    row = self.conn.execute(
                        """SELECT event_id,tenant_id,aggregate_id,payload,attempts
                           FROM memory_outbox
                           WHERE event_type=%s AND processed_at IS NULL
                             AND dead_lettered_at IS NULL
                             AND available_at <= clock_timestamp()
                           ORDER BY created_at,event_id
                           LIMIT 1 FOR UPDATE SKIP LOCKED""",
                        (event_type,),
                    ).fetchone()
                    if row is None:
                        break
                    event_id, tenant, aggregate, payload, attempts = row
                    handler(self.conn, tenant, str(event_id), aggregate, payload)
                    self.conn.execute(
                        """UPDATE memory_outbox
                           SET processed_at=clock_timestamp(),
                               last_attempt_at=clock_timestamp(),
                               attempts=attempts+1,last_error=NULL
                           WHERE event_id=%s""",
                        (event_id,),
                    )
                done += 1
            except Exception as exc:
                if event_id is None:
                    raise
                next_attempt = attempts + 1
                exhausted = next_attempt >= self.max_attempts
                delay = min(
                    self.base_retry_seconds * (2 ** max(0, attempts)),
                    self.max_retry_seconds,
                )
                with self.conn.transaction():
                    self.conn.execute(
                        """UPDATE memory_outbox
                           SET attempts=attempts+1,
                               last_attempt_at=clock_timestamp(),
                               available_at=clock_timestamp()
                                 + (%s * interval '1 second'),
                               dead_lettered_at=CASE WHEN %s
                                 THEN clock_timestamp() ELSE NULL END,
                               last_error=%s
                           WHERE event_id=%s AND processed_at IS NULL""",
                        (delay, exhausted, str(exc)[:1000], event_id),
                    )
                break
        return done

    def retry_dead_letter(self, *, event_id: str,
                          reset_attempts: bool = True) -> bool:
        """Explicitly requeue one dead-lettered event for operator recovery."""
        if not event_id:
            raise ValueError("event_id is required")
        with self.conn.transaction():
            result = self.conn.execute(
                """UPDATE memory_outbox
                   SET dead_lettered_at=NULL,available_at=clock_timestamp(),
                       last_error=NULL,
                       attempts=CASE WHEN %s THEN 0 ELSE attempts END
                   WHERE event_id=%s AND processed_at IS NULL
                     AND dead_lettered_at IS NOT NULL""",
                (reset_attempts, event_id),
            )
        return result.rowcount == 1

    def stats(self, *, event_type: str | None = None) -> dict:
        """Return queue counts grouped by pending, processed and dead-lettered."""
        where = ""
        params = ()
        if event_type is not None:
            if not event_type:
                raise ValueError("event_type cannot be empty")
            where = "WHERE event_type=%s"
            params = (event_type,)
        rows = self.conn.execute(
            f"""SELECT
                  count(*) FILTER (WHERE processed_at IS NULL
                    AND dead_lettered_at IS NULL) AS pending,
                  count(*) FILTER (WHERE processed_at IS NOT NULL) AS processed,
                  count(*) FILTER (WHERE dead_lettered_at IS NOT NULL
                    AND processed_at IS NULL) AS dead_lettered,
                  coalesce(sum(attempts),0) AS total_attempts
                FROM memory_outbox {where}""",
            params,
        ).fetchone()
        return {"pending": int(rows[0]), "processed": int(rows[1]),
                "dead_lettered": int(rows[2]), "total_attempts": int(rows[3])}


def persist_verification_receipt(conn, tenant_id: str, event_id: str,
                                 aggregate_id: str, payload) -> None:
    """Durably record a Temporal verification result; duplicate deliveries are safe."""
    packed = json.dumps(payload, sort_keys=True) if not isinstance(payload, str) else payload
    conn.execute(
        """INSERT INTO memory_events(tenant_id,event_id,event_type,payload)
           VALUES (%s,%s,'memory.claim.verified',%s::jsonb)
           ON CONFLICT(tenant_id,event_id) DO NOTHING""",
        (tenant_id, event_id, packed),
    )
