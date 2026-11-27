"""US-C02: actual Postgres/REST/Worker/Effect continuity with lost provider ACK.

The provider is a deterministic fake remote ledger; this is NOT an AMD/Qwen
or physical-device test. Agent A/B/auditor use separate HTTP client sessions,
but no independently authorized Agent principal is claimed.
"""
from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def test_handoff_to_fenced_effect_unknown_reconcile_and_public_terminal_audit():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("real PostgreSQL MEMORY_TEST_DSN required")
    pytest.importorskip("psycopg")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.worker_runtime import DurableTaskWorker
    from agent_memory_core.worker_leases import LeaseConflict
    from agent_memory_core.provider_contract import ProviderCapabilities

    tenant = "continuity-e2e-" + uuid4().hex
    key = "c02-demo-" + uuid4().hex + uuid4().hex
    device = "bedroom-" + uuid4().hex
    headers = {"Authorization": "Bearer " + key}
    task_type = "continuity.effect.v1"
    effect_key = "apply-bedroom-target-v1"

    def connection():
        return PostgresMemory(dsn)

    db = connection()
    try:
        db.migrate()
    finally:
        db.close()

    app = create_app(api_key=key, tenant_id=tenant, store_factory=connection)
    with TestClient(app) as agent_a:
        created = agent_a.post("/v1/tasks", headers=headers, json={
            "task_type": task_type,
            "input": {"device_id": device, "property": "temperature", "target": 26,
                      "required_effect_keys": [effect_key]},
            "idempotency_key": "create-" + device,
        })
        assert created.status_code == 201, created.text
        task_id = created.json()["task_id"]
        write = agent_a.post("/v1/memories", headers=headers, json={
            "subject_id": device,
            "predicate": "desired_temperature",
            "value": {"temperature": 26},
            "source_id": "synthetic-c02:" + device,
            "valid_from": datetime.now(timezone.utc).isoformat(),
        })
        assert write.status_code == 201, write.text
        offered = agent_a.post("/v1/handoffs", headers=headers, json={
            "task_id": task_id, "source_agent": "agent-A",
            "target_agent": "agent-B", "subject_ids": [device],
            "token_budget": 4096,
        })
        assert offered.status_code == 201, offered.text
        handoff_id = offered.json()["handoff_id"]

    # Only the frozen handoff reaches the next HTTP client; A has exited.
    with TestClient(app) as agent_b:
        accepted = agent_b.post(
            f"/v1/handoffs/{handoff_id}/accept", headers=headers,
            json={"target_agent": "agent-B"})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "accepted"

    provider_calls = []
    reconcile_calls = []
    external = {}

    class LostAckProvider:
        capabilities = ProviderCapabilities(
            idempotency=True, reconciliation=True, lookup_consistency="strong",
            idempotency_retention_seconds=86400)

        def execute(self, request, *, idempotency_key):
            provider_calls.append(idempotency_key)
            # Remote side commits under a stable idempotency key, but ACK vanishes.
            external[idempotency_key] = {
                "provider_reference": "external-" + task_id,
                "result": {"recorded_target": request["value"]}}
            raise TimeoutError("synthetic ACK lost after remote commit")

        def reconcile(self, request, *, idempotency_key, provider_reference):
            reconcile_calls.append(idempotency_key)
            assert request["value"] == 26
            if idempotency_key in external:
                return {"outcome": "committed", **external[idempotency_key]}
            return {"outcome": "unknown"}

    provider = LostAckProvider()

    def handler(task, ctx):
        effects = ctx.list_effects()
        if not effects:
            ctx.commit_checkpoint(
                checkpoint={"phase": "effect_intent_registered",
                            "handoff_id": handoff_id},
                idempotency_key="c02-effect-checkpoint-" + task_id,
                effect_key=effect_key,
                effect_request={"operation": "set_target",
                                "device_id": device, "value": 26})
            outcome = ctx.execute_effect(effect_key=effect_key, provider=provider)
            assert outcome["status"] == "unknown"
            # Returning normally must NOT allow the Worker to finish:
            # the database guards terminal status for unresolved effects.
            return {"simulated": True, "claim_success": True}
        assert len(effects) == 1
        assert effects[0]["effect_key"] == effect_key
        if effects[0]["status"] == "unknown":
            reconciled = ctx.reconcile_effect(effect_key=effect_key, provider=provider)
            assert reconciled["outcome"] == "committed"
        else:
            assert effects[0]["status"] == "committed"
        return {"handoff_id": handoff_id, "effect_key": effect_key,
                "provider_reference_recorded": True}

    worker_a = DurableTaskWorker(
        connection_factory=connection, tenant_id=tenant,
        handlers={task_type: handler}, worker_id="worker-A",
        lease_seconds=30, heartbeat_interval=5)
    with pytest.raises(LeaseConflict, match="Cannot complete task"):
        worker_a.run_once(task_id=task_id)

    with TestClient(app) as observer:
        pending = observer.get(f"/v1/tasks/{task_id}", headers=headers)
        assert pending.status_code == 200
        assert pending.json()["status"] != "completed"
        audit = observer.get(f"/v1/tasks/{task_id}/effects", headers=headers)
        assert audit.status_code == 200, audit.text
        assert audit.json()["items"][0]["status"] == "unknown"
        assert audit.json()["items"][0]["attempt_count"] == 1
        assert audit.json()["items"][0]["causal_effect_verified"] is False

    # Simulate DB-time lease expiration deterministically; no sleep or clock guess.
    db = connection()
    try:
        db.conn.execute(
            """UPDATE memory_durable_tasks
               SET lease_expires_at=clock_timestamp()-interval '1 second'
               WHERE tenant_id=%s AND task_id=%s::uuid""",
            (tenant, task_id))
    finally:
        db.close()

    worker_b = DurableTaskWorker(
        connection_factory=connection, tenant_id=tenant,
        handlers={task_type: handler}, worker_id="worker-B",
        lease_seconds=30, heartbeat_interval=5)
    assert worker_b.run_once(task_id=task_id) is True

    # Auditor C uses only public authenticated read routes, not internal DB.
    with TestClient(app) as auditor_c:
        finished = auditor_c.get(f"/v1/tasks/{task_id}", headers=headers)
        effects = auditor_c.get(f"/v1/tasks/{task_id}/effects", headers=headers)
        assert finished.status_code == 200, finished.text
        assert effects.status_code == 200, effects.text
        task = finished.json()
        item, = effects.json()["items"]
        assert task["status"] == "completed"
        assert task["result"]["handoff_id"] == handoff_id
        assert task["result"]["effect_key"] == effect_key
        assert item["status"] == "committed"
        assert item["attempt_count"] == 1
        assert item["provider_reference_recorded"] is True
        assert item["physical_independence_verified"] is False
        assert item["causal_effect_verified"] is False
    # Feed precisely the two public API responses to the standalone,
    # AMC-package-independent verifier. A ledger PASS is not physical proof.
    import importlib.util
    from pathlib import Path
    probe_path = Path(__file__).resolve().parents[1] / "tools" / "verify_continuity_receipt.py"
    module_spec = importlib.util.spec_from_file_location("c02_public_probe", probe_path)
    public_probe = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(public_probe)
    outcome = public_probe.verify({"task": task, "effects": effects.json()})
    assert outcome["decision"] == "PASS", outcome
    assert outcome["reasons"] == ["LEDGER_CONSISTENT_ONLY"]
    assert outcome["physical_effect_verified"] is False
    assert len(provider_calls) == 1, "UNKNOWN recovery must NOT repeat provider execute"
    assert len(reconcile_calls) == 1
    assert provider_calls[0] == reconcile_calls[0], "Provider key must survive Worker handoff"


def test_c02_task_with_undeclared_effect_cannot_claim_completion():
    """A handler returning success without recording its required effect is blocked."""
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("real PostgreSQL MEMORY_TEST_DSN required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.durable_tasks import DurableTaskStore
    from agent_memory_core.worker_leases import DurableTaskLeaseStore, LeaseConflict

    tenant = "continuity-missing-effect-" + uuid4().hex
    db = PostgresMemory(dsn)
    try:
        db.migrate()
        task = DurableTaskStore(db.conn).create_task(
            tenant_id=tenant, task_type="continuity.effect.v1",
            input={"required_effect_keys": ["must-write-device"],
                   "operation": "synthetic"},
            idempotency_key="missing-" + uuid4().hex)
        lease = DurableTaskLeaseStore(db.conn).claim(
            tenant_id=tenant, task_id=task["task_id"], worker_id="faulty-handler")
        assert lease["task_id"] == task["task_id"]
        with pytest.raises(LeaseConflict, match="required external effects"):
            DurableTaskLeaseStore(db.conn).finish(
                tenant_id=tenant, task_id=task["task_id"],
                worker_id="faulty-handler", lease_epoch=lease["lease_epoch"],
                status="completed", result={"forged": True})
        loaded = DurableTaskStore(db.conn).get_task(
            tenant_id=tenant, task_id=task["task_id"])
        assert loaded["status"] == "running"
    finally:
        db.close()
