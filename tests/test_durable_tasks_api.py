"""Public REST contract for durable tasks; no direct database access by client."""
import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def test_public_api_create_checkpoint_read_and_tenant_isolation():
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
            json={"expected_version":0,"checkpoint":{"last_completed":10},
                  "idempotency_key":"checkpoint-1","progress_completed":10,
                  "progress_total":20,
                  "effect_key":"memory-claim-11",
                  "effect_request":{"action":"write_claim","ordinal":11}})
        assert checkpoint.status_code==201,checkpoint.text
        assert checkpoint.json()["version"]==1

        loaded=client.get(f"/v1/tasks/{task_id}",
                          headers={"Authorization":"Bearer "+key})
        assert loaded.status_code==200
        assert loaded.json()["checkpoint"]["checkpoint"]["last_completed"]==10

        listed=client.get("/v1/tasks",headers={"Authorization":"Bearer "+key})
        assert any(item["task_id"]==task_id for item in listed.json()["items"])

        other_app=create_app(api_key=key,tenant_id=other,store_factory=store_factory)
        with TestClient(other_app) as other_client:
            denied=other_client.get(f"/v1/tasks/{task_id}",
                headers={"Authorization":"Bearer "+key})
            assert denied.status_code==404
