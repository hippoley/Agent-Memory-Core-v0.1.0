"""PostgreSQL-backed durable business tasks and versioned checkpoints.

This module intentionally does not execute external effects. It records an
idempotent effect intent and its Outbox event in the same transaction as the
checkpoint. A provider worker must enforce the effect key at the destination
and reconcile ambiguous outcomes before marking an effect committed.
"""
from __future__ import annotations

import hashlib
import json
import os
from uuid import uuid4

from .temporal_dispatch import stable_workflow_id


class TaskConflict(ValueError):
    """The task/checkpoint version or idempotency contract was violated."""


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()

# Task-type-specific completion policy. Other task types retain their existing
# semantics; this profile opts into strict external-effect declaration.
CONTINUITY_EFFECT_TASK_TYPE = "continuity.effect.v1"


def required_effect_keys(task_type: str, input: dict) -> tuple[str, ...]:
    """Define a bounded manifest of effects needed for this task type.

    The manifest is frozen in the canonical task input. It proves required
    ledger intents were accounted for, NOT that physical causality is proven.
    """
    if task_type != CONTINUITY_EFFECT_TASK_TYPE:
        return ()
    keys = input.get("required_effect_keys")
    if (not isinstance(keys, list) or not 1 <= len(keys) <= 20
            or any(not isinstance(key, str) or not key.strip()
                   or len(key) > 255 for key in keys)
            or len(keys) != len(set(keys))):
        raise ValueError(
            "continuity.effect.v1 requires 1..20 unique, nonblank required_effect_keys")
    return tuple(keys)


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
        required_effect_keys(task_type, input)
        request_hash = _digest({
            "task_type": task_type, "input": input,
            "created_by_principal_id": created_by_principal_id,
            "workflow_id": workflow_id,
        })
        with self.conn.transaction():
            if idempotency_key:
                self.conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s),hashtext(%s))",
                    (tenant_id,idempotency_key),
                )
                existing = self.conn.execute(
                    """SELECT task_id::text,tenant_id,task_type,input,workflow_id,status,
                              checkpoint_version,progress_completed,progress_total,created_at,
                              created_by_principal_id::text
                       FROM memory_durable_tasks
                       WHERE tenant_id=%s AND idempotency_key=%s FOR UPDATE""",
                    (tenant_id, idempotency_key),
                ).fetchone()
                if existing:
                    if _digest({
                        "task_type": existing[2], "input": existing[3],
                        "created_by_principal_id": existing[10],
                        "workflow_id": workflow_id,
                    }) != request_hash:
                        raise TaskConflict("Idempotency key was used for a different task request")
                    return self._task_row(existing[:10])
            task_id = uuid4()
            workflow_id = workflow_id or stable_workflow_id(tenant_id, str(task_id))
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
            # The dispatch intent is committed atomically with the task and
            # creation outbox event. Temporal network calls happen later, outside
            # this transaction, under a leased/fenced dispatcher claim.
            self.conn.execute(
                """INSERT INTO memory_temporal_dispatches
                   (task_id,tenant_id,workflow_id,task_queue)
                   VALUES (%s,%s,%s,%s)""",
                (task_id,tenant_id,workflow_id,
                 os.getenv("MEMORY_TEMPORAL_TASK_QUEUE","agent-memory-core")),
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
        self, *, tenant_id: str, task_id: str, worker_id: str,
        lease_epoch: int, expected_version: int,
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
        from .worker_leases import DurableTaskLeaseStore
        with self.conn.transaction():
            DurableTaskLeaseStore(self.conn).assert_current(
                tenant_id=tenant_id,task_id=task_id,worker_id=worker_id,
                lease_epoch=lease_epoch,lock=True)
            task=self.conn.execute(
                """SELECT checkpoint_version,status FROM memory_durable_tasks
                   WHERE tenant_id=%s AND task_id=%s::uuid FOR UPDATE""",
                (tenant_id,task_id),
            ).fetchone()
            if task is None:
                raise LookupError("Task not found")
            existing=self.conn.execute(
                """SELECT version,checkpoint,artifact_sha256,progress_completed,
                          progress_total,effect_key,effect_request_sha256
                   FROM memory_task_checkpoints
                   WHERE task_id=%s::uuid AND idempotency_key=%s""",
                (task_id,idempotency_key),
            ).fetchone()
            if existing:
                requested_effect_hash = _digest(effect_request) if effect_request is not None else None
                if (
                    _digest(existing[1]) != _digest(checkpoint)
                    or existing[2] != artifact_sha256
                    or existing[3] != progress_completed
                    or existing[4] != progress_total
                    or existing[5] != effect_key
                    or existing[6] != requested_effect_hash
                ):
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
                    """INSERT INTO memory_task_effects(task_id,effect_key,request_sha256,request)
                       VALUES (%s::uuid,%s,%s,%s::jsonb) ON CONFLICT(task_id,effect_key) DO NOTHING""",
                    (task_id,effect_key,effect_hash,_canonical(effect_request)),
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
                   (task_id,version,checkpoint,artifact_sha256,idempotency_key,
                    progress_completed,progress_total,effect_key,effect_request_sha256)
                   VALUES (%s::uuid,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)""",
                (task_id,next_version,_canonical(checkpoint),artifact_sha256,idempotency_key,
                 progress_completed,progress_total,effect_key,
                 _digest(effect_request) if effect_request is not None else None),
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

    def set_status(self, *, tenant_id: str, task_id: str, worker_id: str,
                   lease_epoch: int, status: str,
                   result: dict | None = None, error: dict | None = None) -> bool:
        """Compatibility facade that now requires and enforces Worker Fencing.

        There is deliberately no tenant-only status mutation path. All writes
        delegate to the same lease/epoch-checked store used by the public API.
        """
        from .worker_leases import DurableTaskLeaseStore
        return DurableTaskLeaseStore(self.conn).finish(
            tenant_id=tenant_id,task_id=task_id,worker_id=worker_id,
            lease_epoch=lease_epoch,status=status,result=result,error=error)
