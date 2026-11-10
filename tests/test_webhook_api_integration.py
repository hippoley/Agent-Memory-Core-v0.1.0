"""Tenant-authenticated webhook delivery inspection and replay API tests."""
import os
from datetime import datetime,timezone
from uuid import uuid4
import pytest

@pytest.mark.integration
def test_webhook_delivery_status_replay_and_tenant_isolation():
    httpx=pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.webhooks import WebhookStore,WebhookWorker

    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Real PostgreSQL required")
    db=PostgresMemory(dsn)
    db.migrate()
    tenant="webhook-api-"+uuid4().hex
    master="webhook-api-master-key-minimum-32-characters"
    api_key="webhook-api-tenant-key-"+uuid4().hex
    try:
        sub=WebhookStore(db.conn).create(tenant_id=tenant,
            target_url="https://hooks.example.test/path",
            event_types=["memory.claim.created"])
        memory_id=db.remember(tenant_id=tenant,subject_id="room-"+uuid4().hex,
            predicate="preference",value={"sound":"low"},source_id="dialogue",
            valid_from=datetime.now(timezone.utc))
        transport=httpx.Client(transport=httpx.MockTransport(
            lambda request:httpx.Response(500)))
        worker=WebhookWorker(db.conn,signing_master_key=master,
            allowed_hosts={"hooks.example.test"},max_attempts=1,
            http_client=transport)
        assert worker.consume(limit=1)==1
        delivery_id=db.conn.execute(
            "SELECT delivery_id::text FROM memory_webhook_deliveries WHERE event_id=%s",
            (memory_id,)).fetchone()[0]

        app=create_app(api_key=api_key,tenant_id=tenant,
            store_factory=lambda:PostgresMemory(dsn),
            webhook_signing_key=master,
            webhook_allowed_hosts={"hooks.example.test"})
        client=TestClient(app)
        headers={"Authorization":"Bearer "+api_key}
        deliveries=client.get("/v1/webhooks/"+sub+"/deliveries",headers=headers)
        assert deliveries.status_code==200
        assert deliveries.json()["items"][0]["status"]=="dead"
        replay=client.post("/v1/webhooks/deliveries/"+delivery_id+"/replay",
                           headers=headers)
        assert replay.status_code==200 and replay.json()["requeued"] is True

        other=TestClient(create_app(api_key="another-tenant-api-key-with-32-chars",
            tenant_id="other-"+tenant,store_factory=lambda:PostgresMemory(dsn),
            webhook_signing_key=master,
            webhook_allowed_hosts={"hooks.example.test"}))
        other_headers={"Authorization":"Bearer another-tenant-api-key-with-32-chars"}
        assert other.get("/v1/webhooks/"+sub+"/deliveries",
                         headers=other_headers).json()["items"]==[]
        assert other.post("/v1/webhooks/deliveries/"+delivery_id+"/replay",
                          headers=other_headers).status_code==404
    finally:
        db.close()
