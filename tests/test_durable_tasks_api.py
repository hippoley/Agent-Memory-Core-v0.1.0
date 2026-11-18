"""Public REST contract for durable tasks; no direct database access by client."""
import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def test_public_api_rejects_unleased_checkpoint_and_status_mutations():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory

    key="durable-task-api-key-32-characters-minimum"
    tenant="task-api-"+uuid4().hex
    other="task-api-other-"+uuid4().hex
    def store_factory():
        return PostgresMemory(dsn)
    app=create_app(api_key=key,tenant_id=tenant,store_factory=store_factory)
    with TestClient(app) as client:
        created=client.post("/v1/tasks",headers={"Authorization":"Bearer "+key},
            json={"task_type":"memory.batch.organize","input":{"batch_size":20},
                  "idempotency_key":"create-"+uuid4().hex})
        assert created.status_code==201,created.text
        task_id=created.json()["task_id"]

        checkpoint=client.post(f"/v1/tasks/{task_id}/checkpoints",
            headers={"Authorization":"Bearer "+key},
            json={"worker_id":"untrusted-client","lease_epoch":1,
                  "expected_version":0,"checkpoint":{"last_completed":10},
                  "idempotency_key":"checkpoint-1"})
        assert checkpoint.status_code==403,checkpoint.text

        status=client.post(f"/v1/tasks/{task_id}/status",
            headers={"Authorization":"Bearer "+key},
            json={"worker_id":"untrusted-client","lease_epoch":1,
                  "status":"completed","result":{"forged":True}})
        assert status.status_code==403,status.text

        loaded=client.get(f"/v1/tasks/{task_id}",
                          headers={"Authorization":"Bearer "+key})
        assert loaded.status_code==200
        assert loaded.json()["checkpoint"] is None
        assert loaded.json()["status"]=="queued"

        listed=client.get("/v1/tasks",headers={"Authorization":"Bearer "+key})
        assert any(item["task_id"]==task_id for item in listed.json()["items"])

        other_app=create_app(api_key=key,tenant_id=other,store_factory=store_factory)
        with TestClient(other_app) as other_client:
            denied=other_client.get(f"/v1/tasks/{task_id}",
                headers={"Authorization":"Bearer "+key})
            assert denied.status_code==404


def test_dedicated_worker_credential_claim_checkpoint_and_finish():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory

    from agent_memory_core.durable_tasks import DurableTaskStore
    tenant="task-worker-api-"+uuid4().hex
    token="dedicated-worker-token-"+uuid4().hex
    db=PostgresMemory(dsn)
    try:
        task=DurableTaskStore(db.conn).create_task(
            tenant_id=tenant,task_type="memory.batch.organize",input={"batch_size":1},
            idempotency_key="worker-create-"+uuid4().hex)
        task_id=task["task_id"]
    finally:
        db.close()
    class IdentityStore:
        def authenticate_api_credential(self,secret):
            if secret!=token:
                return None
            return {"tenant_id":tenant,"principal_id":str(uuid4()),
                    "role":"editor","scopes":["tasks:execute"]}
        def authenticate_session(self,token,tenant_id):
            return None
        def close(self):
            pass

    def store_factory():
        return PostgresMemory(dsn)
    app=create_app(store_factory=store_factory,
                   identity_store_factory=IdentityStore)
    headers={"Authorization":"Bearer "+token,"X-Memory-Tenant":tenant}
    with TestClient(app) as client:
        worker_id="worker-"+uuid4().hex

        claimed=client.post("/v1/tasks/worker/claim",headers=headers,
            json={"worker_id":worker_id,"lease_seconds":30})
        assert claimed.status_code==200,claimed.text
        task=claimed.json()["task"]
        assert task["task_id"]==task_id
        assert task["lease_epoch"]==1

        checkpoint=client.post(f"/v1/tasks/{task_id}/checkpoints",
            headers=headers,json={"worker_id":worker_id,
                "lease_epoch":task["lease_epoch"],"expected_version":0,
                "checkpoint":{"cursor":1},"idempotency_key":"worker-checkpoint-1",
                "progress_completed":1,"progress_total":1})
        assert checkpoint.status_code==201,checkpoint.text
        assert checkpoint.json()["version"]==1

        finished=client.post(f"/v1/tasks/{task_id}/status",headers=headers,
            json={"worker_id":worker_id,"lease_epoch":task["lease_epoch"],
                  "status":"completed","result":{"processed":1}})
        assert finished.status_code==200,finished.text
        verify=PostgresMemory(dsn)
        try:
            loaded=DurableTaskStore(verify.conn).get_task(tenant_id=tenant,task_id=task_id)
            assert loaded["status"]=="completed"
        finally:
            verify.close()


def test_memory_wildcard_credential_cannot_claim_tasks_without_explicit_scope():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app

    tenant="task-worker-deny-"+uuid4().hex
    token="memory-wildcard-"+uuid4().hex
    class IdentityStore:
        def authenticate_api_credential(self,secret):
            if secret!=token:
                return None
            return {"tenant_id":tenant,"principal_id":str(uuid4()),
                    "role":"editor","scopes":["memory:*"]}
        def authenticate_session(self,token,tenant_id):
            return None
        def close(self):
            pass

    app=create_app(store_factory=lambda:None,identity_store_factory=IdentityStore)
    with TestClient(app) as client:
        denied=client.post("/v1/tasks/worker/claim",
            headers={"Authorization":"Bearer "+token,"X-Memory-Tenant":tenant},
            json={"worker_id":"ordinary-memory-client","lease_seconds":30})
        assert denied.status_code==403,denied.text
        assert "tasks:execute" in denied.text
