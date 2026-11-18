"""Atomic PostgreSQL leases and fencing for durable business task workers.

A lease is advisory ownership; the monotonically increasing epoch is the
safety boundary. Every worker mutation must present both owner and epoch.
All lease comparisons use PostgreSQL's clock, never a worker wall clock.
"""
from __future__ import annotations

from datetime import timedelta
from uuid import uuid4


class LeaseConflict(RuntimeError):
    """The lease is absent, expired, or belongs to a newer worker epoch."""


class DurableTaskLeaseStore:
    def __init__(self, conn):
        self.conn = conn

    @staticmethod
    def _row(row):
        if row is None:
            return None
        return {
            "task_id": row[0], "tenant_id": row[1], "task_type": row[2],
            "input": row[3], "workflow_id": row[4], "status": row[5],
            "checkpoint_version": row[6], "progress_completed": row[7],
            "progress_total": row[8], "lease_owner": row[9],
            "lease_epoch": row[10], "lease_expires_at": row[11],
            "checkpoint": row[12],
        }

    def claim(self, *, tenant_id: str, worker_id: str | None = None,
              lease_seconds: int = 30) -> dict | None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        owner = worker_id or str(uuid4())
        if not owner or len(owner) > 255:
            raise ValueError("worker_id must contain 1..255 characters")
        with self.conn.transaction():
            row = self.conn.execute(
                """WITH candidate AS (
                     SELECT task_id
                     FROM memory_durable_tasks
                     WHERE tenant_id=%s
                       AND status IN ('queued','running')
                       AND (lease_expires_at IS NULL OR lease_expires_at <= clock_timestamp())
                     ORDER BY created_at,task_id
                     FOR UPDATE SKIP LOCKED
                     LIMIT 1
                   )
                   UPDATE memory_durable_tasks AS task
                   SET status='running',
                       lease_owner=%s,
                       lease_epoch=task.lease_epoch+1,
                       lease_expires_at=clock_timestamp()+(%s * interval '1 second'),
                       heartbeat_at=clock_timestamp(),
                       started_at=coalesce(task.started_at,clock_timestamp()),
                       updated_at=clock_timestamp()
                   FROM candidate
                   WHERE task.task_id=candidate.task_id
                   RETURNING task.task_id::text,task.tenant_id,task.task_type,
                     task.input,task.workflow_id,task.status,task.checkpoint_version,
                     task.progress_completed,task.progress_total,task.lease_owner,
                     task.lease_epoch,task.lease_expires_at""",
                (tenant_id, owner, lease_seconds),
            ).fetchone()
            if row is None:
                return None
            checkpoint = self.conn.execute(
                """SELECT checkpoint FROM memory_task_checkpoints
                   WHERE task_id=%s::uuid ORDER BY version DESC LIMIT 1""",
                (row[0],),
            ).fetchone()
            values = tuple(row) + (checkpoint[0] if checkpoint else None,)
            return self._row(values)

    def renew(self, *, tenant_id: str, task_id: str, worker_id: str,
              lease_epoch: int, lease_seconds: int = 30) -> bool:
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        row = self.conn.execute(
            """UPDATE memory_durable_tasks
               SET lease_expires_at=clock_timestamp()+(%s * interval '1 second'),
                   heartbeat_at=clock_timestamp(),updated_at=clock_timestamp()
               WHERE tenant_id=%s AND task_id=%s::uuid
                 AND lease_owner=%s AND lease_epoch=%s
                 AND lease_expires_at > clock_timestamp()
                 AND status='running'
               RETURNING task_id""",
            (lease_seconds,tenant_id,task_id,worker_id,lease_epoch),
        ).fetchone()
        return row is not None

    def assert_current(self, *, tenant_id: str, task_id: str, worker_id: str,
                       lease_epoch: int, lock: bool = False) -> None:
        suffix = " FOR UPDATE" if lock else ""
        row = self.conn.execute(
            """SELECT 1 FROM memory_durable_tasks
               WHERE tenant_id=%s AND task_id=%s::uuid
                 AND lease_owner=%s AND lease_epoch=%s
                 AND lease_expires_at > clock_timestamp()
                 AND status='running'""" + suffix,
            (tenant_id,task_id,worker_id,lease_epoch),
        ).fetchone()
        if row is None:
            raise LeaseConflict("Worker lease is expired, revoked, or fenced by a newer epoch")

    def commit_checkpoint(self, *, tenant_id: str, task_id: str, worker_id: str,
                         lease_epoch: int, expected_version: int, checkpoint: dict,
                         idempotency_key: str, progress_completed: int = 0,
                         progress_total: int = 0, artifact_sha256: str | None = None,
                         effect_key: str | None = None,
                         effect_request: dict | None = None) -> dict:
        from .durable_tasks import DurableTaskStore
        with self.conn.transaction():
            self.assert_current(tenant_id=tenant_id,task_id=task_id,
                                worker_id=worker_id,lease_epoch=lease_epoch,lock=True)
            return DurableTaskStore(self.conn).commit_checkpoint(
                tenant_id=tenant_id,task_id=task_id,expected_version=expected_version,
                checkpoint=checkpoint,idempotency_key=idempotency_key,
                progress_completed=progress_completed,progress_total=progress_total,
                artifact_sha256=artifact_sha256,effect_key=effect_key,
                effect_request=effect_request,
            )

    def finish(self, *, tenant_id: str, task_id: str, worker_id: str,
               lease_epoch: int, status: str, result: dict | None = None,
               error: dict | None = None) -> bool:
        if status not in {"completed","failed","paused","cancelled"}:
            raise ValueError("Worker may finish only as completed, failed, paused, or cancelled")
        terminal = status in {"completed","failed","cancelled"}
        with self.conn.transaction():
            self.assert_current(tenant_id=tenant_id,task_id=task_id,
                                worker_id=worker_id,lease_epoch=lease_epoch,lock=True)
            row = self.conn.execute(
                """UPDATE memory_durable_tasks
                   SET status=%s,result=%s::jsonb,last_error=%s::jsonb,
                       completed_at=CASE WHEN %s THEN clock_timestamp() ELSE NULL END,
                       lease_owner=NULL,lease_expires_at=NULL,
                       updated_at=clock_timestamp()
                   WHERE tenant_id=%s AND task_id=%s::uuid
                     AND lease_owner=%s AND lease_epoch=%s
                   RETURNING task_id""",
                (status,
                 __import__("json").dumps(result,sort_keys=True) if result is not None else None,
                 __import__("json").dumps(error,sort_keys=True) if error is not None else None,
                 terminal,tenant_id,task_id,worker_id,lease_epoch),
            ).fetchone()
            if row is None:
                raise LeaseConflict("Worker was fenced before task completion")
            self.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'task',%s,'memory.task.status.changed',%s::jsonb)""",
                (uuid4(),tenant_id,task_id,
                 __import__("json").dumps({"task_id":task_id,"status":status,
                                           "terminal":terminal},sort_keys=True)),
            )
            return True
