"""Tenant-scoped Temporal workflow REST contract tests."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from agent_memory_core.api import create_app


class FakeWorkflowHandle:
    def __init__(self, workflow_id):
        self.id=workflow_id
        self.result_run_id="run-001"
        self.cancelled=False
    async def describe(self):
        return SimpleNamespace(
            run_id="run-001",
            status=SimpleNamespace(name="RUNNING"),
            workflow_type="VerifyMemoryWorkflow",
        )
    async def cancel(self):
        self.cancelled=True


class FakeTemporalClient:
    def __init__(self):
        self.started=[]
        self.handles={}
    async def start_workflow(self, workflow, request, *, id, task_queue):
        self.started.append((workflow,request,id,task_queue))
        handle=FakeWorkflowHandle(id)
        self.handles[id]=handle
        return handle
    def get_workflow_handle(self, workflow_id):
        if workflow_id not in self.handles:
            raise KeyError(workflow_id)
        return self.handles[workflow_id]


def make_client(tenant="tenant-a", temporal=True):
    key="test-temporal-api-key-with-sufficient-length"
    fake=FakeTemporalClient()
    async def factory():
        return fake
    app=create_app(
        api_key=key,
        tenant_id=tenant,
        temporal_client_factory=factory if temporal else None,
        temporal_task_queue="agent-memory-core" if temporal else None,
    )
    return TestClient(app),{"Authorization":"Bearer "+key},fake


def test_temporal_workflow_start_describe_cancel_and_tenant_scope():
    client,headers,fake=make_client()
    memory_id=str(uuid4())
    started=client.post("/v1/workflows/memory-verifications",
        headers=headers,json={"memory_id":memory_id,"workflow_id":"check-001"})
    assert started.status_code==202,started.text
    body=started.json()
    workflow_id=body["workflow_id"]
    assert workflow_id=="tenant-a:check-001"
    assert body["status"]=="STARTED"
    assert fake.started[0][0]=="VerifyMemoryWorkflow"
    assert fake.started[0][1]=={"tenant_id":"tenant-a","memory_id":memory_id}
    assert fake.started[0][3]=="agent-memory-core"

    described=client.get("/v1/workflows/"+workflow_id,headers=headers)
    assert described.status_code==200,described.text
    assert described.json()["status"]=="RUNNING"

    cancelled=client.post("/v1/workflows/"+workflow_id+"/cancel",headers=headers)
    assert cancelled.status_code==200,cancelled.text
    assert fake.handles[workflow_id].cancelled is True

    # Another tenant cannot inspect or cancel this execution.
    other,other_headers,_=make_client(tenant="tenant-b")
    assert other.get("/v1/workflows/"+workflow_id,headers=other_headers).status_code==404
    assert other.post("/v1/workflows/"+workflow_id+"/cancel",
                      headers=other_headers).status_code==404


def test_temporal_api_returns_503_when_worker_runtime_not_configured():
    client,headers,_=make_client(temporal=False)
    response=client.post("/v1/workflows/memory-verifications",
        headers=headers,json={"memory_id":str(uuid4())})
    assert response.status_code==503


def test_temporal_api_rejects_invalid_memory_uuid():
    client,headers,_=make_client()
    response=client.post("/v1/workflows/memory-verifications",
        headers=headers,json={"memory_id":"not-a-uuid"})
    assert response.status_code==422
