import pytest
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from types import SimpleNamespace
from agent_memory_core.api import create_app


class FakeWorkflowHandle:
    def __init__(self, workflow_id):
        self.id=workflow_id
        self.result_run_id="run-001"
        self.cancelled=False
    async def describe(self):
        return SimpleNamespace(status=SimpleNamespace(name="RUNNING"),
                               run_id="run-001",workflow_type="VerifyMemoryWorkflow")
    async def cancel(self):
        self.cancelled=True


class FakeTemporalClient:
    def __init__(self):
        self.handles={}
        self.started=None
    async def start_workflow(self, workflow, request, *, id, task_queue):
        self.started=(request,id,task_queue)
        handle=FakeWorkflowHandle(id)
        self.handles[id]=handle
        return handle
    def get_workflow_handle(self, workflow_id):
        if workflow_id not in self.handles:
            raise LookupError("unknown workflow")
        return self.handles[workflow_id]


@pytest.mark.parametrize("workflow_id",["requested-001",None])
def test_temporal_workflow_start_status_cancel_and_tenant_scope(workflow_id):
    temporal=FakeTemporalClient()
    async def factory():
        return temporal
    key="temporal-api-key-with-more-than-24-characters"
    app=create_app(api_key=key,tenant_id="tenant-a",
        temporal_client_factory=factory,temporal_task_queue="memory-test")
    client=TestClient(app)
    headers={"Authorization":"Bearer "+key}
    payload={"memory_id":"123e4567-e89b-12d3-a456-426614174000"}
    if workflow_id:
        payload["workflow_id"]=workflow_id
    started=client.post("/v1/workflows/memory-verifications",
                        headers=headers,json=payload)
    assert started.status_code==202,started.text
    body=started.json()
    assert body["workflow_id"].startswith("tenant-a:")
    assert body["run_id"]=="run-001"
    assert temporal.started[0]["tenant_id"]=="tenant-a"
    assert temporal.started[1]==body["workflow_id"]
    assert temporal.started[2]=="memory-test"

    status=client.get("/v1/workflows/"+body["workflow_id"],headers=headers)
    assert status.status_code==200,status.text
    assert status.json()["status"]=="RUNNING"
    assert status.json()["run_id"]=="run-001"

    cancelled=client.post("/v1/workflows/"+body["workflow_id"]+"/cancel",
                          headers=headers)
    assert cancelled.status_code==200,cancelled.text
    assert temporal.handles[body["workflow_id"]].cancelled

    other_key="other-temporal-api-key-with-more-than-24"
    other=create_app(api_key=other_key,tenant_id="tenant-b",
        temporal_client_factory=factory,temporal_task_queue="memory-test")
    denied=TestClient(other).get("/v1/workflows/"+body["workflow_id"],
        headers={"Authorization":"Bearer "+other_key})
    assert denied.status_code==404


def test_temporal_api_fails_closed_when_runtime_is_not_configured():
    key="temporal-api-key-with-more-than-24-characters"
    client=TestClient(create_app(api_key=key,tenant_id="home"))
    response=client.post("/v1/workflows/memory-verifications",
        headers={"Authorization":"Bearer "+key},
        json={"memory_id":"123e4567-e89b-12d3-a456-426614174000"})
    assert response.status_code==503
