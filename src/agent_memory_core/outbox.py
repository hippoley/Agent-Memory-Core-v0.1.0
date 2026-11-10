"""Transactional PostgreSQL outbox consumer.

Only database-local handlers can be treated as atomic with acknowledgement.
External APIs require their own stable idempotency keys and reconciliation.
"""
from __future__ import annotations
from datetime import timedelta
from uuid import UUID
import json

class OutboxWorker:
    def __init__(self, conn):
        self.conn=conn

    def consume(self, *, event_type: str, handler, limit: int=20) -> int:
        if not event_type or not (1 <= limit <= 100):
            raise ValueError("Event type and limit required")
        done=0
        for _ in range(limit):
            event_id=None
            try:
                with self.conn.transaction():
                    row=self.conn.execute(
                        """SELECT event_id,tenant_id,aggregate_id,payload
                           FROM memory_outbox
                           WHERE event_type=%s AND processed_at IS NULL
                             AND available_at <= clock_timestamp()
                           ORDER BY created_at,event_id
                           LIMIT 1 FOR UPDATE SKIP LOCKED""",
                        (event_type,)).fetchone()
                    if row is None:
                        break
                    event_id,tenant,aggregate,payload=row
                    # The handler must use this connection and must not commit:
                    # effects and acknowledgement share a PostgreSQL transaction.
                    handler(self.conn,tenant,str(event_id),aggregate,payload)
                    self.conn.execute(
                        """UPDATE memory_outbox
                           SET processed_at=clock_timestamp(),attempts=attempts+1,
                               last_error=NULL
                           WHERE event_id=%s""",(event_id,))
                    done+=1
            except Exception as exc:
                if event_id is None:
                    raise
                with self.conn.transaction():
                    self.conn.execute(
                        """UPDATE memory_outbox
                           SET attempts=attempts+1,
                               available_at=clock_timestamp()+interval '5 seconds',
                               last_error=%s
                           WHERE event_id=%s AND processed_at IS NULL""",
                        (str(exc)[:500],event_id))
                # Do not spin on an errored event; next poll will retry.
                break
        return done


def persist_verification_receipt(conn, tenant_id: str, event_id: str,
                                 aggregate_id: str, payload) -> None:
    """Durably record a Temporal verification result; duplicate deliveries are safe."""
    packed=json.dumps(payload,sort_keys=True) if not isinstance(payload,str) else payload
    conn.execute(
        """INSERT INTO memory_events(tenant_id,event_id,event_type,payload)
           VALUES (%s,%s,'memory.claim.verified',%s::jsonb)
           ON CONFLICT(tenant_id,event_id) DO NOTHING""",
        (tenant_id,event_id,packed))


class ProjectionOutboxWorker:
    """Independent, replayable PostgreSQL-local deliveries per consumer.

    A handler must perform its database effects through this connection and
    must not commit. External HTTP delivery requires its own idempotency
    receipt because a remote acknowledgement cannot share this transaction.
    """

    def __init__(self, conn):
        self.conn = conn

    def consume(self, *, consumer_id: str, event_type: str, handler,
                limit: int = 20, max_attempts: int = 5) -> int:
        if (not consumer_id or not event_type or not 1 <= limit <= 100
                or not 1 <= max_attempts <= 100):
            raise ValueError("Valid consumer, event type, limit, and retry budget required")
        # Register outstanding events for this consumer without touching
        # another consumer's acknowledgement, including already-processed
        # legacy outbox rows.
        with self.conn.transaction():
            self.conn.execute(
                """INSERT INTO memory_outbox_deliveries(event_id,consumer_id)
                   SELECT o.event_id,%s FROM memory_outbox o
                   WHERE o.event_type=%s AND NOT EXISTS (
                     SELECT 1 FROM memory_outbox_deliveries d
                     WHERE d.event_id=o.event_id AND d.consumer_id=%s
                   )
                   ORDER BY o.created_at,o.event_id LIMIT %s
                   ON CONFLICT(event_id,consumer_id) DO NOTHING""",
                (consumer_id,event_type,consumer_id,min(limit*10,1000)),
            )
        processed = 0
        for _ in range(limit):
            event_id = None
            try:
                with self.conn.transaction():
                    row = self.conn.execute(
                        """SELECT d.event_id,o.tenant_id,o.aggregate_id,o.payload
                           FROM memory_outbox_deliveries d
                           JOIN memory_outbox o ON o.event_id=d.event_id
                           WHERE d.consumer_id=%s AND o.event_type=%s
                             AND d.delivered_at IS NULL
                             AND d.dead_letter_at IS NULL
                             AND d.available_at<=clock_timestamp()
                           ORDER BY o.created_at,d.event_id
                           LIMIT 1 FOR UPDATE OF d SKIP LOCKED""",
                        (consumer_id,event_type),
                    ).fetchone()
                    if row is None:
                        break
                    event_id, tenant, aggregate, payload = row
                    handler(self.conn,tenant,str(event_id),aggregate,payload)
                    self.conn.execute(
                        """UPDATE memory_outbox_deliveries SET
                           delivered_at=clock_timestamp(),attempts=attempts+1,
                           last_error=NULL WHERE event_id=%s AND consumer_id=%s""",
                        (event_id,consumer_id),
                    )
                    processed += 1
            except Exception as exc:
                if event_id is None:
                    raise
                # Handler writes were rolled back with the failed transaction.
                # Capture retry metadata separately from the failed side effect.
                with self.conn.transaction():
                    self.conn.execute(
                        """UPDATE memory_outbox_deliveries SET
                           attempts=attempts+1,
                           available_at=clock_timestamp() +
                               (interval '1 second' * LEAST(
                                   300, (5 * POWER(2,LEAST(attempts,6)))::integer)),
                           dead_letter_at=CASE WHEN attempts+1 >= %s
                               THEN clock_timestamp() ELSE NULL END,
                           last_error=%s
                           WHERE event_id=%s AND consumer_id=%s
                             AND delivered_at IS NULL AND dead_letter_at IS NULL""",
                        (max_attempts,str(exc)[:500],event_id,consumer_id),
                    )
                break
        return processed

    def retry_dead_letter(self, *, tenant_id: str, consumer_id: str,
                          event_id: str) -> bool:
        """Operator-controlled recovery after correcting the root cause."""
        if not all((tenant_id,consumer_id,event_id)):
            raise ValueError("Tenant, consumer, and event required")
        with self.conn.transaction():
            row = self.conn.execute(
                """UPDATE memory_outbox_deliveries d SET
                   attempts=0,dead_letter_at=NULL,last_error=NULL,
                   available_at=clock_timestamp()
                   FROM memory_outbox o
                   WHERE d.event_id=o.event_id AND o.tenant_id=%s
                     AND d.consumer_id=%s AND d.event_id=%s
                     AND d.dead_letter_at IS NOT NULL
                     AND d.delivered_at IS NULL
                   RETURNING d.event_id""",
                (tenant_id,consumer_id,event_id),
            ).fetchone()
        return row is not None
