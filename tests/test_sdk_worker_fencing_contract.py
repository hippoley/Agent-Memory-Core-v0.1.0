"""SDK <-> actual Worker API request contract, not a physical-effect test."""
import json
import httpx
import pytest
from agent_memory_core.sdk import MemoryClient, MemoryAPIError


def test_worker_claim_renew_checkpoint_finish_and_independent_read():
    calls = []
    task_id = "11111111-1111-4111-8111-111111111111"
    def handler(req):
        body = json.loads(req.content or b"{}")
        calls.append((req.method, req.url.path, req.headers, body))
        if req.url.path == "/v1/tasks/worker/claim":
            return httpx.Response(200, json={"task":{"task_id":task_id, "lease_epoch":3}})
        if req.url.path.endswith("/lease/renew"):
            return httpx.Response(200, json={"renewed":True})
        if req.url.path.endswith("/checkpoints"):
            return httpx.Response(201, json={"version":1})
        if req.url.path.endswith("/status"):
            return httpx.Response(200, json={"status":"completed"})
        return httpx.Response(200, json={"task_id":task_id,"status":"completed"})

    with MemoryClient("https://amc.example", "worker-token",
                      tenant_id="tenant-1", transport=httpx.MockTransport(handler)) as worker:
        claim = worker.claim_task(worker_id="worker-1", lease_seconds=30)["task"]
        assert claim["lease_epoch"] == 3
        worker.renew_task_lease(task_id, worker_id="worker-1", lease_epoch=3)
        cp = worker.commit_task_checkpoint(task_id, worker_id="worker-1", lease_epoch=3,
                                           expected_version=0, checkpoint={"cursor":1},
                                           idempotency_key="stable-checkpoint",
                                           effect_key="stable-effect",
                                           effect_request={"type":"device.command","value":26})
        assert cp["version"] == 1
        worker.set_task_status(task_id, worker_id="worker-1", lease_epoch=3,
                               status="completed", result={"receipt_id":"r1"})
        assert worker.get_task(task_id)["status"] == "completed"

    assert [x[1] for x in calls] == [
        "/v1/tasks/worker/claim", f"/v1/tasks/{task_id}/lease/renew",
        f"/v1/tasks/{task_id}/checkpoints", f"/v1/tasks/{task_id}/status",
        f"/v1/tasks/{task_id}"]
    for _, path, headers, payload in calls[:4]:
        assert headers["x-memory-tenant"] == "tenant-1"
        assert headers["authorization"] == "Bearer worker-token"
        assert payload["worker_id"] == "worker-1"
    assert calls[2][3]["lease_epoch"] == 3
    assert calls[2][2]["idempotency-key"] == "stable-checkpoint"
    assert calls[3][3]["lease_epoch"] == 3


@pytest.mark.parametrize("worker,epoch", [
    ("",1), ("worker",0), ("worker",-1), ("worker",True), ("worker","1")])
def test_worker_mutations_reject_invalid_fencing_before_network(worker, epoch):
    hits = []
    def handler(req):
        hits.append(req)
        return httpx.Response(200,json={})
    with MemoryClient("http://amc.local","token",transport=httpx.MockTransport(handler)) as sdk:
        with pytest.raises(ValueError):
            sdk.set_task_status("task",worker_id=worker,lease_epoch=epoch,status="completed")
        with pytest.raises(ValueError):
            sdk.commit_task_checkpoint("task",worker_id=worker,lease_epoch=epoch,
                                       expected_version=0,checkpoint={},
                                       idempotency_key="once")
    assert hits == []


def test_sdk_does_not_hide_server_fencing_conflict():
    def handler(req):
        return httpx.Response(409,json={"detail":"Lease expired or worker was fenced"})
    with MemoryClient("http://amc.local","token",
                      transport=httpx.MockTransport(handler)) as sdk:
        with pytest.raises(MemoryAPIError) as exc:
            sdk.set_task_status("task-1",worker_id="stale",lease_epoch=1,
                                status="completed")
    assert exc.value.status_code == 409


def test_worker_claim_requires_nonempty_identity_and_valid_ttl():
    with MemoryClient("http://amc.local","token",
                      transport=httpx.MockTransport(lambda _: pytest.fail("network called"))) as sdk:
        for worker,ttl in [("",30),("agent",0),("agent",3601),("agent",True)]:
            with pytest.raises(ValueError):
                sdk.claim_task(worker_id=worker,lease_seconds=ttl)
