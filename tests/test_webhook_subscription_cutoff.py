"""New webhook subscriptions start at creation time, not the tenant's full history."""
import os
from datetime import datetime,timezone
from uuid import uuid4
import pytest

@pytest.mark.integration
def test_webhook_subscription_does_not_backfill_pre_subscription_events():
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.webhooks import WebhookStore,WebhookWorker
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Real PostgreSQL required")
    db=PostgresMemory(dsn)
    db.migrate()
    tenant="webhook-cutoff-"+uuid4().hex
    try:
        old=db.remember(tenant_id=tenant,subject_id="room-old",
            predicate="fact",value={"v":1},source_id="before",
            valid_from=datetime.now(timezone.utc))
        sub=WebhookStore(db.conn).create(tenant_id=tenant,
            target_url="https://hooks.example.test/new-only",
            event_types=["memory.claim.created"])
        new=db.remember(tenant_id=tenant,subject_id="room-new",
            predicate="fact",value={"v":2},source_id="after",
            valid_from=datetime.now(timezone.utc))
        worker=WebhookWorker(db.conn,
            signing_master_key="webhook-cutoff-master-key-at-least-32-chars",
            allowed_hosts={"hooks.example.test"})
        assert worker.enqueue(tenant_id=tenant)==1
        rows=db.conn.execute(
            """SELECT event_id::text FROM memory_webhook_deliveries
               WHERE subscription_id=%s::uuid""",(sub,)).fetchall()
        assert rows==[(new,)]
        assert old not in [r[0] for r in rows]
    finally:
        db.close()
