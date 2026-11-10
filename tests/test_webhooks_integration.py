"""Real PostgreSQL webhook subscription, signature, retry and tenant tests."""
import hashlib
import hmac
import json
import os
from datetime import datetime,timezone
from uuid import uuid4
import pytest

pytestmark=pytest.mark.integration


def database():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    db=PostgresMemory(dsn)
    db.migrate()
    return db


def test_signed_webhook_delivery_and_tenant_scoped_revocation():
    httpx=pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.webhooks import WebhookWorker

    db=database()
    tenant="webhook-"+uuid4().hex
    api_key="webhook-api-key-for-tenant-"+uuid4().hex
    master="webhook-master-signing-key-at-least-32-characters"
    try:
        from agent_memory_core.postgres import PostgresMemory
        dsn=os.environ["MEMORY_TEST_DSN"]
        app=create_app(api_key=api_key,tenant_id=tenant,
            store_factory=lambda:PostgresMemory(dsn),
            webhook_signing_key=master,
            webhook_allowed_hosts={"hooks.example.test"})
        client=TestClient(app)
        headers={"Authorization":"Bearer "+api_key}
        created=client.post("/v1/webhooks",headers=headers,json={
            "target_url":"https://hooks.example.test/memory/events",
            "event_types":["memory.claim.created"]})
        assert created.status_code==201,created.text
        sub=created.json()
        assert len(sub["signing_secret"])==64
        listed=client.get("/v1/webhooks",headers=headers)
        assert listed.status_code==200 and len(listed.json()["items"])==1

        mid=db.remember(tenant_id=tenant,subject_id="room-"+uuid4().hex,
            predicate="preference",value={"noise":"quiet"},source_id="dialogue:1",
            valid_from=datetime.now(timezone.utc))
        received=[]
        def receiver(request):
            body=request.content
            timestamp=request.headers["X-Memory-Timestamp"]
            expected=hmac.new(bytes.fromhex(sub["signing_secret"]),
                timestamp.encode()+b"."+body,hashlib.sha256).hexdigest()
            assert hmac.compare_digest(request.headers["X-Memory-Signature"],"sha256="+expected)
            envelope=json.loads(body)
            assert envelope["event_id"]==mid
            assert envelope["tenant_id"]==tenant
            received.append(envelope)
            return httpx.Response(204)
        transport=httpx.Client(transport=httpx.MockTransport(receiver))
        worker=WebhookWorker(db.conn,signing_master_key=master,
            allowed_hosts={"hooks.example.test"},http_client=transport)
        assert worker.consume(limit=10)==1
        assert len(received)==1
        state=db.conn.execute(
            """SELECT status,attempts,response_status FROM memory_webhook_deliveries
               WHERE event_id=%s""",(mid,)).fetchone()
        assert state==("delivered",1,204)
        assert worker.consume(limit=10)==0

        # A different tenant cannot list or revoke this subscription.
        other=TestClient(create_app(api_key="other-webhook-key-strong-enough",
            tenant_id="other-"+tenant,store_factory=lambda:PostgresMemory(dsn),
            webhook_signing_key=master,
            webhook_allowed_hosts={"hooks.example.test"}))
        other_headers={"Authorization":"Bearer other-webhook-key-strong-enough"}
        assert other.get("/v1/webhooks",headers=other_headers).json()["items"]==[]
        assert other.delete("/v1/webhooks/"+sub["subscription_id"],
                            headers=other_headers).status_code==404
        assert client.delete("/v1/webhooks/"+sub["subscription_id"],
                             headers=headers).status_code==200
    finally:
        db.close()


def test_webhook_failure_backoff_dead_letter_and_operator_replay():
    httpx=pytest.importorskip("httpx")
    from agent_memory_core.webhooks import WebhookStore,WebhookWorker

    db=database()
    tenant="webhook-retry-"+uuid4().hex
    master="webhook-master-signing-key-at-least-32-characters"
    try:
        sub=WebhookStore(db.conn).create(tenant_id=tenant,
            target_url="https://hooks.example.test/retry",
            event_types=["memory.claim.created"])
        mid=db.remember(tenant_id=tenant,subject_id="room-"+uuid4().hex,
            predicate="preference",value={"noise":"quiet"},source_id="dialogue:1",
            valid_from=datetime.now(timezone.utc))
        transport=httpx.Client(transport=httpx.MockTransport(
            lambda request:httpx.Response(503)))
        worker=WebhookWorker(db.conn,signing_master_key=master,
            allowed_hosts={"hooks.example.test"},max_attempts=2,http_client=transport)
        assert worker.consume(limit=1)==1
        db.conn.execute(
            """UPDATE memory_webhook_deliveries SET available_at=clock_timestamp()
               WHERE event_id=%s""",(mid,))
        db.conn.commit()
        assert worker.consume(limit=1)==1
        row=db.conn.execute(
            """SELECT status,attempts FROM memory_webhook_deliveries
               WHERE event_id=%s""",(mid,)).fetchone()
        assert row==("dead",2)

        assert worker.requeue_dead(tenant_id=tenant,
            delivery_id=db.conn.execute(
                "SELECT delivery_id::text FROM memory_webhook_deliveries WHERE event_id=%s",
                (mid,)).fetchone()[0])
        transport.close()
        success=httpx.Client(transport=httpx.MockTransport(
            lambda request:httpx.Response(202)))
        replay=WebhookWorker(db.conn,signing_master_key=master,
            allowed_hosts={"hooks.example.test"},max_attempts=2,http_client=success)
        assert replay.consume(limit=1)==1
        row=db.conn.execute(
            """SELECT status,attempts,response_status FROM memory_webhook_deliveries
               WHERE event_id=%s""",(mid,)).fetchone()
        assert row==("delivered",1,202)
    finally:
        db.close()


def test_webhook_target_allowlist_rejects_ssrf_and_plain_http():
    from agent_memory_core.webhooks import validate_target
    allowed={"hooks.example.test"}
    for url in [
        "http://hooks.example.test/callback",
        "https://127.0.0.1/callback",
        "https://169.254.169.254/latest/meta-data",
        "https://hooks.example.test.evil.invalid/callback",
        "https://user:pass@hooks.example.test/callback",
        "https://hooks.example.test/callback?redirect=https://127.0.0.1",
        "https://hooks.example.test/callback#fragment",
    ]:
        with pytest.raises(ValueError):
            validate_target(url,allowed)
