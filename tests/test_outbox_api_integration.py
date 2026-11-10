"""Dead outbox replay must be authenticated and tenant scoped."""
import os
from uuid import uuid4
import pytest

@pytest.mark.integration
def test_outbox_dead_letter_replay_api_is_tenant_scoped():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL required")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.outbox import OutboxWorker
    dsn=os.environ["MEMORY_TEST_DSN"]
    tenant="outbox-api-"+uuid4().hex
    event=uuid4()
    db=PostgresMemory(dsn)
    try:
        db.migrate()
        with db.conn.transaction():
            db.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'claim','sample','memory.api.dead','{}'::jsonb)""",
                (event,tenant))
        worker=OutboxWorker(db.conn,max_attempts=1)
        def fail(*args):
            raise RuntimeError("operator replay fixture")
        assert worker.consume(event_type="memory.api.dead",handler=fail,
            limit=1,tenant_id=tenant)==0
        state=db.conn.execute(
            "SELECT dead_lettered_at FROM memory_outbox WHERE event_id=%s",(event,)).fetchone()
        assert state[0] is not None
    finally:
        db.close()
    key="outbox-api-key-"+uuid4().hex
    app=TestClient(create_app(api_key=key,tenant_id=tenant,
        store_factory=lambda:PostgresMemory(dsn)))
    headers={"Authorization":"Bearer "+key}
    assert app.post(f"/v1/events/outbox/{event}/replay",headers=headers).json()=={
        "event_id":str(event),"requeued":True}
    other_key="other-outbox-key-"+uuid4().hex
    other=TestClient(create_app(api_key=other_key,tenant_id="other-"+tenant,
        store_factory=lambda:PostgresMemory(dsn)))
    response=other.post(f"/v1/events/outbox/{event}/replay",
        headers={"Authorization":"Bearer "+other_key})
    assert response.status_code==404
