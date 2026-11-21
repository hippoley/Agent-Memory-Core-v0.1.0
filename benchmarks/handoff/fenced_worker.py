"""Disposable, separately executed fenced Worker for US-C01 receipt experiment.

Uses the actual DurableTaskLeaseStore and checkpoint/outbox transaction.
Not an Agent B credential: the test harness runs this worker separately.
"""
from __future__ import annotations
import argparse
import json
import os
from uuid import uuid4

from agent_memory_core.postgres import PostgresMemory
from agent_memory_core.worker_leases import DurableTaskLeaseStore


def register_intent(*, tenant_id: str, task_id: str, handoff_id: str,
                    subject_id: str, period: str) -> dict:
    request = {
        "effect_key": "supplier-approval-request",
        "request_id": handoff_id + ":supplier-approval-request",
        "payload": {"subject": subject_id, "period": period},
    }
    worker_id = "handoff-probe-" + uuid4().hex
    db = PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        store = DurableTaskLeaseStore(db.conn)
        claimed = store.claim(
            tenant_id=tenant_id, worker_id=worker_id,
            task_id=task_id, lease_seconds=120,
        )
        if claimed is None:
            raise RuntimeError("Worker lease unavailable; will not dispatch effect")
        if claimed["task_id"] != task_id or claimed["tenant_id"] != tenant_id:
            raise RuntimeError("Worker claimed wrong task or tenant")
        checkpoint = {
            "phase": "effect-intent-registered",
            "handoff_id": handoff_id,
            "effect_request": request,
        }
        result = store.commit_checkpoint(
            tenant_id=tenant_id, task_id=task_id, worker_id=worker_id,
            lease_epoch=claimed["lease_epoch"],
            expected_version=claimed["checkpoint_version"],
            checkpoint=checkpoint,
            idempotency_key="handoff-intent:" + handoff_id,
            effect_key=request["effect_key"],
            effect_request=request,
        )
        return {"task_id": task_id, "lease_epoch": claimed["lease_epoch"],
                "checkpoint_version": result["version"],
                "effect_request": request, "replayed": result["replayed"]}
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--handoff-id", required=True)
    parser.add_argument("--subject-id", required=True)
    parser.add_argument("--period", required=True)
    args = parser.parse_args()
    print(json.dumps(register_intent(
        tenant_id=os.environ["MEMORY_TENANT_ID"], task_id=args.task_id,
        handoff_id=args.handoff_id, subject_id=args.subject_id,
        period=args.period,
    )))
