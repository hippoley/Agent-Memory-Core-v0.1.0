"""PostgreSQL-backed durable business tasks and versioned checkpoints.

This module intentionally does not execute external effects. It records an
idempotent effect intent and its Outbox event in the same transaction as the
checkpoint. A provider worker must enforce the effect key at the destination
and reconcile ambiguous outcomes before marking an effect committed.
"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4


class TaskConflict(ValueError):
    """The task/checkpoint version or idempotency contract was violated."""


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class DurableTaskStore:
    def __init__(self, conn):
        self.conn = conn

    def create_task(
        self, *, tenant_id: str, task_type: str, input: dict,
        created_by_principal_id: str | None = None,
        workflow_id: str | None = None, idempotency_key: str | None = None,
    ) -> dict:
        if not tenant_id or not task_type or not isinstance(input, dict):
            raise ValueError("tenant_id, task_type and object input are required")
        request_hash = _digest({
            "task_type": task_type, "input": input,
            "created_by_principal_id": created_by_principal_id,
            "workflow_id": workflow_id,
        })
        with self.conn.transaction():
            if idempotency_key:
                existing = self.conn.execute(
                    """SELECT task_id::text,tenant_id,task_type,input,workflow_id,status,
                              checkpoint_version,progress_completed,progress_total,created_at
                       FROM memory_durable_tasks
                       WHERE tenant_id=%s AND idempotency_key=%s FOR UPDATE""",
                    (tenant_id, idempotency_key),
                ).fetchone()
                if existing:
                    if _digest({
                        "task_type": existing[2], "input": existing[3],
                        "created_by_principal_id": created_by_principal_id,
                        "workflow_id": existing[4],
                    }) != request_hash:
                        raise TaskConflict("Idempotency key was used for a different task request")
                    return self._task_row(existing)
            task_id = uuid4()
            row = self.conn.execute(
                """INSERT INTO memory_durable_tasks
                   (task_id,tenant_id,created_by_principal_id,task_type,workflow_id,
                    input,idempotency_key)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)
                   RETURNING task_id::text,tenant_id,task_type,input,workflow_id,status,
                             checkpoint_version,progress_completed,progress_total,created_at""",
                (task_id,tenant_id,created_by_principal_id,task_type,workflow_id,
                 _canonical(input),idempotency_key),
            ).fetchone()
            event_id = uuid4()
            self.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'task',%s,'memory.task.created',%s::jsonb)""",
                (event_id,tenant_id,str(task_id),_canonical({
                    "task_id": str(task_id), "task_type": task_type,
                    "workflow_id": workflow_id,
                })),
            )
            return self._task_row(row)

    @staticmethod
    def _task_row(row) -> dict:
        keys = ("task_id","tenant_id","task_type","input","workflow_id","status",
                "checkpoint_version","progress_completed","progress_total","created_at")
        return dict(zip(keys,row))

    def list_tasks(self, *, tenant_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("limit must be 1..100 and offset non-negative")
        rows=self.conn.execute(
            """SELECT task_id::text,tenant_id,task_type,input,workflow_id,status,
                      checkpoint_version,progress_completed,progress_total,created_at
               FROM memory_durable_tasks WHERE tenant_id=%s
               ORDER BY created_at DESC,task_id LIMIT %s OFFSET %s""",
            (tenant_id,limit,offset),
        ).fetchall()
        return [self._task_row(row) for row in rows]

    def get_task(self, *, tenant_id: str, task_id: str) -> dict | None:
        row = self.conn.execute(
            """SELECT task_id::text,tenant_id,task_type,input,workflow_id,status,
                      checkpoint_version,progress_completed,progress_total,created_at,
                      updated_at,completed_at,result,last_error
               FROM memory_durable_tasks WHERE tenant_id=%s AND task_id=%s::uuid""",
            (tenant_id,task_id),
        ).fetchone()
        if row is None:
            return None
        keys=("task_id","tenant_id","task_type","input","workflow_id","status",
              "checkpoint_version","progress_completed","progress_total","created_at",
              "updated_at","completed_at","result","last_error")
        task=dict(zip(keys,row))
        checkpoint=self.conn.execute(
            """SELECT version,checkpoint,artifact_sha256,created_at
               FROM memory_task_checkpoints WHERE task_id=%s
               ORDER BY version DESC LIMIT 1""",(task_id,)
        ).fetchone()
        task["checkpoint"] = (dict(zip(
            ("version","checkpoint","artifact_sha256","created_at"),checkpoint
        )) if checkpoint else None)
        return task

    def commit_checkpoint(
        self, *, tenant_id: str, task_id: str, expected_version: int,
        checkpoint: dict, idempotency_key: str,
        progress_completed: int = 0, progress_total: int = 0,
        artifact_sha256: str | None = None,
        effect_key: str | None = None, effect_request: dict | None = None,
    ) -> dict:
        if expected_version < 0 or progress_completed < 0 or progress_total < 0:
            raise ValueError("Versions and progress must be non-negative")
        if progress_total and progress_completed > progress_total:
            raise ValueError("progress_completed cannot exceed progress_total")
        if not idempotency_key or not isinstance(checkpoint,dict):
            raise ValueError("idempotency_key and object checkpoint are required")
        if (effect_key is None) != (effect_request is None):
            raise ValueError("effect_key and effect_request must be supplied together")
        if artifact_sha256 is not None and (
            len(artifact_sha256) != 64 or any(c not in "0123456789abcdef" for c in artifact_sha256)
        ):
            raise ValueError("artifact_sha256 must be lowercase SHA-256 hex")
        with self.conn.transaction():
            task=self.conn.execute(
                """SELECT checkpoint_version,status FROM memory_durable_tasks
                   WHERE tenant_id=%s AND task_id=%s::uuid FOR UPDATE""",
                (tenant_id,task_id),
            ).fetchone()
            if task is None:
                raise LookupError("Task not found")
            existing=self.conn.execute(
                """SELECT version,checkpoint,artifact_sha256 FROM memory_task_checkpoints
                   WHERE task_id=%s::uuid AND idempotency_key=%s""",
                (task_id,idempotency_key),
            ).fetchone()
            if existing:
                if _digest(existing[1]) != _digest(checkpoint) or existing[2] != artifact_sha256:
                    raise TaskConflict("Checkpoint idempotency key was reused with different content")
                return {"task_id":task_id,"version":existing[0],"replayed":True}
            current_version,status=task
            if current_version != expected_version:
                raise TaskConflict(f"Checkpoint version conflict: expected {expected_version}, current {current_version}")
            if status in ("completed","failed","cancelled"):
                raise TaskConflict(f"Cannot checkpoint terminal task in state {status}")
            if effect_key is not None:
                effect_hash=_digest(effect_request)
                previous=self.conn.execute(
                    """SELECT request_sha256 FROM memory_task_effects
                       WHERE task_id=%s::uuid AND effect_key=%s FOR UPDATE""",
                    (task_id,effect_key),
                ).fetchone()
                if previous and previous[0] != effect_hash:
                    raise TaskConflict("Effect key was reused with a different request")
                self.conn.execute(
                    """INSERT INTO memory_task_effects(task_id,effect_key,request_sha256)
                       VALUES (%s::uuid,%s,%s) ON CONFLICT(task_id,effect_key) DO NOTHING""",
                    (task_id,effect_key,effect_hash),
                )
                if not previous:
                    self.conn.execute(
                        """INSERT INTO memory_outbox
                           (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                           VALUES (%s,%s,'task',%s,'memory.task.effect.requested',%s::jsonb)""",
                        (uuid4(),tenant_id,task_id,_canonical({
                            "task_id":task_id,"effect_key":effect_key,
                            "request":effect_request,"request_sha256":effect_hash,
                        })),
                    )
            next_version=current_version+1
            self.conn.execute(
                """INSERT INTO memory_task_checkpoints
                   (task_id,version,checkpoint,artifact_sha256,idempotency_key)
                   VALUES (%s::uuid,%s,%s::jsonb,%s,%s)""",
                (task_id,next_version,_canonical(checkpoint),artifact_sha256,idempotency_key),
            )
            self.conn.execute(
                """UPDATE memory_durable_tasks
                   SET checkpoint_version=%s,status='running',
                       progress_completed=%s,progress_total=%s,
                       started_at=coalesce(started_at,clock_timestamp()),
                       updated_at=clock_timestamp()
                   WHERE tenant_id=%s AND task_id=%s::uuid""",
                (next_version,progress_completed,progress_total,tenant_id,task_id),
            )
            self.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'task',%s,'memory.task.checkpoint.committed',%s::jsonb)""",
                (uuid4(),tenant_id,task_id,_canonical({
                    "task_id":task_id,"checkpoint_version":next_version,
                    "progress_completed":progress_completed,"progress_total":progress_total,
                    "artifact_sha256":artifact_sha256,
                })),
            )
            return {"task_id":task_id,"version":next_version,"replayed":False}

    def set_status(self, *, tenant_id: str, task_id: str, status: str,
                   result: dict | None = None, error: dict | None = None) -> bool:
        allowed={"queued","running","paused","completed","failed","cancelled"}
        if status not in allowed:
            raise ValueError("Unsupported task status")
        terminal=status in {"completed","failed","cancelled"}
        with self.conn.transaction():
            row=self.conn.execute(
                """UPDATE memory_durable_tasks SET status=%s,result=%s::jsonb,
                   last_error=%s::jsonb,updated_at=clock_timestamp(),
                   completed_at=CASE WHEN %s THEN clock_timestamp() ELSE NULL END
                   WHERE tenant_id=%s AND task_id=%s::uuid
                     AND status NOT IN ('completed','failed','cancelled')
                   RETURNING task_id""",
                (status,_canonical(result) if result is not None else None,
                 _canonical(error) if error is not None else None,terminal,tenant_id,task_id),
            ).fetchone()
            return row is not None
