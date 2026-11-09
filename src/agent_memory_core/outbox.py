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
