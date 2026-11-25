"""Public redacted effect read: fail closed on tenant and sensitive payloads."""
import os
from uuid import uuid4

import httpx
import pytest
from agent_memory_core.durable_effects import public_effect_snapshot
from agent_memory_core.sdk import MemoryClient, MemoryAPIError


def test_audit_projection_never_exposes_requests_or_provider_secrets():
    effect = {
        "effect_key": "set-room-temp", "request_sha256": "a" * 64,
        "status": "committed", "attempt_count": 1,
        "provider_reference": "SECRET-PROVIDER-TOKEN",
        "request": {"access_token": "SECRET-ACTUATION"},
        "provider_result": {"secret": "PRIVATE-RAW-RESPONSE"},
        "last_error": {"url": "PRIVATE-INTERNAL-URL"},
        "execution_worker_id": "PRIVATE-WORKER",
        "created_at": "2026-11-25T00:00:00Z",
        "updated_at": "2026-11-25T00:01:00Z",
        "resolved_at": "2026-11-25T00:01:00Z"}
    output = public_effect_snapshot(effect)
    assert output["status"] == "committed"
    assert output["request_sha256"] == "a" * 64
    assert output["provider_reference_recorded"] is True
    assert output["physical_independence_verified"] is False
    assert output["causal_effect_verified"] is False
    assert set(output).isdisjoint(
        {"request", "provider_result", "provider_reference", "last_error", "execution_worker_id"})
    assert "SECRET" not in str(output) and "PRIVATE" not in str(output)


def test_sdk_get_task_effects_uses_read_only_route_and_propagates_404():
    task_id = "11111111-1111-4111-8111-111111111111"
    seen = []
    def handler(request):
        seen.append((request.method, request.url.path, request.headers.get("x-memory-tenant")))
        if len(seen) == 1:
            return httpx.Response(200, json={"task_id": task_id, "items":[]})
        return httpx.Response(404, json={"detail": "Task not found"})
    with MemoryClient("https://amc.example", "reader-token", tenant_id="tenant-a",
                      transport=httpx.MockTransport(handler)) as client:
        assert client.get_task_effects(task_id)["items"] == []
        with pytest.raises(MemoryAPIError) as exc:
            client.get_task_effects(task_id)
    assert exc.value.status_code == 404
    assert seen == [("GET", f"/v1/tasks/{task_id}/effects", "tenant-a")] * 2


@pytest.mark.integration
def test_effect_audit_is_tenant_scoped_with_real_postgres():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.durable_tasks import DurableTaskStore
    from agent_memory_core.worker_leases import DurableTaskLeaseStore
    from agent_memory_core.durable_effects import DurableTaskEffectStore

    tenant, outsider = "effect-audit-" + uuid4().hex, "effect-outsider-" + uuid4().hex
    key = "secret-" + uuid4().hex + uuid4().hex
    def factory():
        return PostgresMemory(dsn)
    db = factory()
    try:
        db.migrate()
        task = DurableTaskStore(db.conn).create_task(
            tenant_id=tenant, task_type="audit.fixture",
            input={"operation":"test"}, idempotency_key="audit-" + uuid4().hex)
        task_id = task["task_id"]
        leased = DurableTaskLeaseStore(db.conn).claim(
            tenant_id=tenant, worker_id="audit-worker", task_id=task_id)
        DurableTaskLeaseStore(db.conn).commit_checkpoint(
            tenant_id=tenant, task_id=task_id,
            worker_id="audit-worker", lease_epoch=leased["lease_epoch"],
            expected_version=0, checkpoint={"stage":"requested"},
            idempotency_key="request-effect",
            effect_key="audit-effect",
            effect_request={"token":"MUST-NOT-LEAK","target":26})
    finally:
        db.close()

    app = create_app(api_key=key, tenant_id=tenant, store_factory=factory)
    outsider_app = create_app(api_key=key, tenant_id=outsider, store_factory=factory)
    headers = {"Authorization":"Bearer " + key}
    with TestClient(app) as client:
        response = client.get(f"/v1/tasks/{task_id}/effects", headers=headers)
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["status"] == "requested"
        assert items[0]["request_sha256"]
        assert "MUST-NOT-LEAK" not in response.text
        assert "request" not in items[0]
        invalid = client.get("/v1/tasks/not-a-uuid/effects", headers=headers)
        assert invalid.status_code == 422
    with TestClient(outsider_app) as client:
        denied = client.get(f"/v1/tasks/{task_id}/effects", headers=headers)
        assert denied.status_code == 404
