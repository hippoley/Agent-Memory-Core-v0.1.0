"""Test-only independent recovery process using AMC's existing effect state machine.

Fault injection simulates the old worker's lease expiry after its process has
exited. Neither the Provider ledger nor AMC status is modified by a shortcut.
A new fenced Worker must make every state transition.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen
from uuid import uuid4

from agent_memory_core.postgres import PostgresMemory
from agent_memory_core.durable_effects import DurableTaskEffectStore
from agent_memory_core.worker_leases import DurableTaskLeaseStore


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def recover(*, tenant_id: str, task_id: str, effect_key: str,
            old_worker_id: str, old_epoch: int, provider_url: str) -> dict:
    db = PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        # Test-only fault injection: precisely expire the known old lease.
        # A production Worker would instead await natural PostgreSQL expiry.
        expired = db.conn.execute(
            """UPDATE memory_durable_tasks
               SET lease_expires_at=clock_timestamp()-interval '1 second'
               WHERE tenant_id=%s AND task_id=%s::uuid
                 AND lease_owner=%s AND lease_epoch=%s
                 AND status='running'
               RETURNING task_id""",
            (tenant_id, task_id, old_worker_id, old_epoch),
        ).fetchone()
        if expired is None:
            raise RuntimeError("Old fenced lease identity mismatch")
        db.conn.commit()

        owner = "handoff-recovery-" + uuid4().hex
        leases = DurableTaskLeaseStore(db.conn)
        claim = leases.claim(
            tenant_id=tenant_id, worker_id=owner, task_id=task_id,
            lease_seconds=120,
        )
        if claim is None or claim["lease_epoch"] <= old_epoch:
            raise RuntimeError("New recovery Worker did not obtain a newer epoch")
        effects = DurableTaskEffectStore(db.conn)
        count = effects.mark_stale_executions_unknown(
            tenant_id=tenant_id, task_id=task_id, worker_id=owner,
            lease_epoch=claim["lease_epoch"],
        )
        if count != 1:
            raise RuntimeError("Expected exactly one stale execution to become UNKNOWN")
        unknown = effects.get_effect(task_id=task_id, effect_key=effect_key)
        if unknown is None or unknown["status"] != "unknown":
            raise RuntimeError("Effect did not become UNKNOWN")
        intent = effects.begin_reconciliation(
            tenant_id=tenant_id, task_id=task_id, effect_key=effect_key,
            worker_id=owner, lease_epoch=claim["lease_epoch"],
        )
        request = intent["request"]
        if request.get("effect_key") != effect_key:
            raise RuntimeError("Reconciliation request identity mismatch")
        digest = hashlib.sha256(_canonical(request).encode()).hexdigest()
        if digest != intent["request_sha256"]:
            raise RuntimeError("Reconciliation request SHA-256 mismatch")
        rid = request["request_id"]
        try:
            url = provider_url.rstrip("/") + "/effects/" + quote(rid, safe="")
            with urlopen(url, timeout=5) as response:
                receipt = json.load(response)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            # A 404 is only an observation; not proof an old request cannot
            # still be in flight. Keep the current request UNKNOWN.
            effects.resolve_reconciliation(
                tenant_id=tenant_id, task_id=task_id, effect_key=effect_key,
                worker_id=owner, lease_epoch=claim["lease_epoch"],
                outcome="unknown",
                error={"reason": "PROVIDER_RECEIPT_UNAVAILABLE", "type": type(exc).__name__},
            )
            return {"classification": "UNKNOWN", "reexecuted": False}
        if (receipt.get("status") != "committed"
                or receipt.get("request_id") != rid
                or receipt.get("effect_key") != effect_key
                or receipt.get("payload") != request.get("payload")):
            effects.resolve_reconciliation(
                tenant_id=tenant_id, task_id=task_id, effect_key=effect_key,
                worker_id=owner, lease_epoch=claim["lease_epoch"],
                outcome="unknown",
                error={"reason": "PROVIDER_RECEIPT_CONFLICT"},
            )
            return {"classification": "CONFLICT", "reexecuted": False}
        effects.resolve_reconciliation(
            tenant_id=tenant_id, task_id=task_id, effect_key=effect_key,
            worker_id=owner, lease_epoch=claim["lease_epoch"],
            outcome="committed", provider_reference=rid,
            provider_result=receipt,
        )
        final = effects.get_effect(task_id=task_id, effect_key=effect_key)
        if final is None or final["status"] != "committed":
            raise RuntimeError("Failed to persist reconciled effect receipt")
        # This is an explicit fenced task completion, never an inference by B.
        leases.finish(
            tenant_id=tenant_id, task_id=task_id, worker_id=owner,
            lease_epoch=claim["lease_epoch"], status="completed",
            result={"effect_key": effect_key, "provider_reference": rid},
        )
        return {"classification": "COMMITTED", "reexecuted": False,
                "lease_epoch": claim["lease_epoch"], "provider_reference": rid}
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("task-id", "effect-key", "old-worker-id", "old-epoch", "provider-url"):
        parser.add_argument("--" + name, required=True,
                            type=int if name == "old-epoch" else str)
    args = parser.parse_args()
    print(json.dumps(recover(
        tenant_id=os.environ["MEMORY_TENANT_ID"], task_id=args.task_id,
        effect_key=args.effect_key, old_worker_id=args.old_worker_id,
        old_epoch=args.old_epoch, provider_url=args.provider_url,
    )))
