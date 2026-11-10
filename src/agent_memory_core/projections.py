"""Durable per-channel projection fanout for PostgreSQL-local handlers.

Delivery is at least once. Handlers must use the supplied connection, and
must not commit or make external side effects inside the transaction.
"""
from __future__ import annotations

from collections.abc import Callable


class ProjectionWorker:
    def __init__(self, conn, *, max_attempts: int = 5):
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.conn = conn
        self.max_attempts = max_attempts

    def enqueue(self, *, event_type: str, channels: tuple[str, ...]) -> int:
        """Materialize independent tasks for existing and future outbox rows.

        Safe to call repeatedly; do not filter on the legacy processed_at
        flag, because one consumer's acknowledgement does not imply fanout.
        """
        if not event_type or not channels or any(not c or not c.strip() for c in channels):
            raise ValueError("event_type and non-empty channels required")
        if len(set(channels)) != len(channels):
            raise ValueError("Duplicate channel")
        added = 0
        with self.conn.transaction():
            for channel in channels:
                result = self.conn.execute(
                    """INSERT INTO memory_projection_deliveries(event_id,channel)
                       SELECT event_id,%s FROM memory_outbox WHERE event_type=%s
                       ON CONFLICT (event_id,channel) DO NOTHING""",
                    (channel, event_type))
                added += result.rowcount
        return added

    def consume(self, *, channel: str, handler: Callable, limit: int = 20) -> int:
        """Atomically commit a DB-local projection and its acknowledgement.

        A failed attempt rolls back the handler transaction, then records
        bounded retry state in a separate transaction. A dead task is never
        silently acknowledged. Work is claimed via SKIP LOCKED.
        """
        if not channel or not channel.strip() or not 1 <= limit <= 100:
            raise ValueError("Invalid channel or batch limit")
        completed = 0
        for _ in range(limit):
            event_id = None
            try:
                with self.conn.transaction():
                    row = self.conn.execute(
                        """SELECT d.event_id,o.tenant_id,o.aggregate_id,o.event_type,o.payload
                           FROM memory_projection_deliveries d
                           JOIN memory_outbox o ON o.event_id=d.event_id
                           WHERE d.channel=%s AND d.status='pending'
                             AND d.available_at<=clock_timestamp()
                           ORDER BY d.available_at,o.created_at,d.event_id
                           LIMIT 1 FOR UPDATE OF d SKIP LOCKED""",
                        (channel,)).fetchone()
                    if row is None:
                        break
                    event_id, tenant, aggregate, event_type, payload = row
                    handler(self.conn, tenant, str(event_id), aggregate, event_type, payload)
                    self.conn.execute(
                        """UPDATE memory_projection_deliveries
                           SET status='done',processed_at=clock_timestamp(),
                               attempts=attempts+1,last_error=NULL
                           WHERE event_id=%s AND channel=%s""", (event_id,channel))
                completed += 1
            except Exception as exc:
                if event_id is None:
                    raise
                with self.conn.transaction():
                    self.conn.execute(
                        """UPDATE memory_projection_deliveries
                           SET attempts=attempts+1,
                               status=CASE WHEN attempts+1 >= %s THEN 'dead' ELSE 'pending' END,
                               available_at=clock_timestamp()
                                  + (LEAST(300,5 * power(2,LEAST(attempts,6))) * interval '1 second'),
                               last_error=%s
                           WHERE event_id=%s AND channel=%s AND status='pending'""",
                        (self.max_attempts,str(exc)[:500],event_id,channel))
                # Avoid repeatedly hitting a broken handler within one poll.
                break
        return completed

    def requeue_dead(self, *, event_id: str, channel: str) -> bool:
        """Operator-controlled replay; reset budget, retaining event identity."""
        if not event_id or not channel:
            raise ValueError("event_id and channel required")
        with self.conn.transaction():
            row = self.conn.execute(
                """UPDATE memory_projection_deliveries SET status='pending',
                   attempts=0,available_at=clock_timestamp(),last_error=NULL,
                   processed_at=NULL WHERE event_id=%s AND channel=%s
                   AND status='dead' RETURNING event_id""",
                (event_id,channel)).fetchone()
        return row is not None
