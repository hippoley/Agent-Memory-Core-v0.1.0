"""Leased, idempotent dispatch ledger for Temporal Workflow starts.

The dispatcher claims in PostgreSQL, calls Temporal outside the transaction,
then records the run using a fenced lease epoch. If the Temporal start succeeds
but the acknowledgement is lost, the lease expires and the next dispatcher
must describe/reuse the deterministic Workflow ID rather than create a new ID.
"""
from __future__ import annotations
from uuid import uuid5, NAMESPACE_URL


def stable_workflow_id(tenant_id: str, task_id: str) -> str:
    if not tenant_id or not task_id:
        raise ValueError("tenant_id and task_id are required")
    return "amc-" + str(uuid5(NAMESPACE_URL, f"agent-memory-core:{tenant_id}:{task_id}"))


class TemporalDispatchConflict(RuntimeError):
    pass


class TemporalDispatchStore:
    def __init__(self, conn):
        self.conn=conn

    def enqueue(self, *, tenant_id: str, task_id: str, workflow_id: str,
                task_queue: str="agent-memory-core") -> dict:
        if not tenant_id or not task_id or not workflow_id or not task_queue:
            raise ValueError("tenant, task, workflow and task queue are required")
        row=self.conn.execute(
            """INSERT INTO memory_temporal_dispatches
               (task_id,tenant_id,workflow_id,task_queue)
               VALUES (%s::uuid,%s,%s,%s)
               ON CONFLICT(task_id) DO UPDATE SET task_id=EXCLUDED.task_id
               WHERE memory_temporal_dispatches.tenant_id=EXCLUDED.tenant_id
                 AND memory_temporal_dispatches.workflow_id=EXCLUDED.workflow_id
                 AND memory_temporal_dispatches.task_queue=EXCLUDED.task_queue
               RETURNING task_id::text,tenant_id,workflow_id,task_queue,status""",
            (task_id,tenant_id,workflow_id,task_queue)).fetchone()
        if row is None:
            raise TemporalDispatchConflict("Task already has a different Temporal dispatch identity")
        return dict(zip(("task_id","tenant_id","workflow_id","task_queue","status"),row))

    def claim(self, *, dispatcher_id: str, lease_seconds: int=30) -> dict | None:
        if not dispatcher_id or not 1 <= lease_seconds <= 3600:
            raise ValueError("dispatcher_id and lease_seconds 1..3600 are required")
        with self.conn.transaction():
            row=self.conn.execute(
                """SELECT task_id::text,tenant_id,workflow_id,task_queue,lease_epoch
                   FROM memory_temporal_dispatches
                   WHERE status='pending'
                      OR (status='dispatching' AND lease_expires_at<=clock_timestamp())
                   ORDER BY created_at,task_id
                   LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()
            if row is None:
                return None
            task_id,tenant_id,workflow_id,task_queue,epoch=row
            claimed=self.conn.execute(
                """UPDATE memory_temporal_dispatches
                   SET status='dispatching',lease_owner=%s,lease_epoch=lease_epoch+1,
                       lease_expires_at=clock_timestamp()+(%s * interval '1 second'),
                       attempts=attempts+1,updated_at=clock_timestamp()
                   WHERE task_id=%s::uuid
                   RETURNING task_id::text,tenant_id,workflow_id,task_queue,lease_epoch,attempts""",
                (dispatcher_id,lease_seconds,task_id)).fetchone()
        return dict(zip(("task_id","tenant_id","workflow_id","task_queue","lease_epoch","attempts"),claimed))

    def mark_started(self, *, task_id: str, dispatcher_id: str,
                     lease_epoch: int, temporal_run_id: str) -> bool:
        if not temporal_run_id:
            raise ValueError("Temporal run ID is required")
        with self.conn.transaction():
            row=self.conn.execute(
                """UPDATE memory_temporal_dispatches
                   SET status='started',temporal_run_id=%s,started_at=clock_timestamp(),
                       lease_owner=NULL,lease_expires_at=NULL,last_error=NULL,
                       updated_at=clock_timestamp()
                   WHERE task_id=%s::uuid AND status='dispatching'
                     AND lease_owner=%s AND lease_epoch=%s
                   RETURNING task_id""",
                (temporal_run_id,task_id,dispatcher_id,lease_epoch)).fetchone()
        return row is not None

    def release_for_retry(self, *, task_id: str, dispatcher_id: str,
                          lease_epoch: int, error: str) -> bool:
        with self.conn.transaction():
            row=self.conn.execute(
                """UPDATE memory_temporal_dispatches
                   SET status='pending',lease_owner=NULL,lease_expires_at=NULL,
                       last_error=%s,updated_at=clock_timestamp()
                   WHERE task_id=%s::uuid AND status='dispatching'
                     AND lease_owner=%s AND lease_epoch=%s
                   RETURNING task_id""",
                (str(error)[:2000],task_id,dispatcher_id,lease_epoch)).fetchone()
        return row is not None
