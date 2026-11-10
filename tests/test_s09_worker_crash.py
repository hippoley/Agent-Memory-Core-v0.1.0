"""S09: kill a real webhook worker process and recover leased delivery.

Requires actual PostgreSQL and Linux-style SIGKILL. No HTTP mock is used
to simulate a process crash: a separate Python interpreter is terminated.
"""
import os
import signal
import subprocess
import sys
from datetime import datetime,timezone
from uuid import uuid4
import pytest

pytestmark=pytest.mark.integration

def test_sigkill_after_durable_claim_is_recovered_and_stale_ack_is_fenced():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Real PostgreSQL required")
    if os.name=="nt":
        pytest.skip("SIGKILL semantics require a POSIX test runner")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.webhooks import WebhookStore,WebhookWorker
    db=PostgresMemory(dsn)
    tenant="s09-kill-"+uuid4().hex
    key="s09-master-signature-key-minimum-32-characters"
    try:
        db.migrate()
        sub=WebhookStore(db.conn).create(tenant_id=tenant,
            target_url="https://hooks.example.test/agent",
            event_types=["memory.claim.created"])
        mid=db.remember(tenant_id=tenant,subject_id="bedroom",
            predicate="night_mode",value={"enabled":True},source_id="dialogue",
            valid_from=datetime(2026,1,1,tzinfo=timezone.utc))
        worker=WebhookWorker(db.conn,signing_master_key=key,
            allowed_hosts={"hooks.example.test"},lease_seconds=30)
        assert worker.enqueue(tenant_id=tenant)==1

        # A genuinely independent interpreter claims and COMMITS the lease
        # before SIGKILL. No cleanup or connection-close acknowledgement runs.
        program="""
import os,signal
from agent_memory_core.postgres import PostgresMemory
from agent_memory_core.webhooks import WebhookWorker
db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
worker=WebhookWorker(db.conn,signing_master_key=os.environ["TEST_WEBHOOK_KEY"],
    allowed_hosts={"hooks.example.test"},lease_seconds=30)
row=worker._claim(tenant_id=os.environ["TEST_TENANT"])
assert row is not None
os.kill(os.getpid(),signal.SIGKILL)
"""
        env=os.environ.copy()
        env["TEST_TENANT"]=tenant
        env["TEST_WEBHOOK_KEY"]=key
        child=subprocess.run([sys.executable,"-c",program],env=env,
            capture_output=True,text=True,timeout=15)
        assert child.returncode in (-signal.SIGKILL,128+signal.SIGKILL),child.stderr
        delivery=db.conn.execute(
            """SELECT delivery_id::text,status,attempts,lease_until FROM
               memory_webhook_deliveries WHERE subscription_id=%s::uuid""",
            (sub,)).fetchone()
        assert delivery[1]=="delivering" and delivery[2]==1
        assert delivery[3] is not None
        assert worker._claim(tenant_id=tenant) is None

        # Move only the database lease clock boundary; a second independent
        # worker reclaims the same durable delivery, attempts becomes 2.
        db.conn.execute(
            """UPDATE memory_webhook_deliveries SET lease_until=clock_timestamp()
                  -interval '1 second' WHERE delivery_id=%s::uuid""",
            (delivery[0],))
        claimed=worker._claim(tenant_id=tenant)
        assert claimed and claimed[0]==delivery[0] and claimed[7]==2
        assert db.conn.execute(
            "SELECT attempts FROM memory_webhook_deliveries WHERE delivery_id=%s::uuid",
            (delivery[0],)).fetchone()[0]==2

        # A delayed acknowledgement from the killed/stale attempt cannot
        # overwrite the new lease or report a false delivery.
        worker._finish(delivery[0],success=True,status=204,error=None,attempt=1)
        state=db.conn.execute(
            """SELECT status,attempts,delivered_at FROM memory_webhook_deliveries
               WHERE delivery_id=%s::uuid""",(delivery[0],)).fetchone()
        assert state==("delivering",2,None)

        worker._finish(delivery[0],success=True,status=204,error=None,attempt=2)
        done=db.conn.execute(
            """SELECT status,attempts,response_status,delivered_at
               FROM memory_webhook_deliveries WHERE delivery_id=%s::uuid""",
            (delivery[0],)).fetchone()
        assert done[0:3]==("delivered",2,204) and done[3] is not None
        assert worker._claim(tenant_id=tenant) is None
    finally:
        db.close()


def test_tenant_backlog_drains_once_without_duplicate_deliveries():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Real PostgreSQL required")
    httpx=pytest.importorskip("httpx")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.webhooks import WebhookStore,WebhookWorker

    db=PostgresMemory(dsn)
    tenant="s09-backlog-"+uuid4().hex
    try:
        db.migrate()
        sub=WebhookStore(db.conn).create(tenant_id=tenant,
            target_url="https://hooks.example.test/backlog",
            event_types=["memory.claim.created"])
        for i in range(64):
            db.remember(tenant_id=tenant,subject_id=f"room-{i}",
                predicate="temperature",value={"celsius":20+i%4},
                source_id=f"sensor:{i}",valid_from=datetime(2026,1,1,tzinfo=timezone.utc))
        seen=[]
        def receiver(req):
            seen.append(req.headers["X-Memory-Delivery"])
            return httpx.Response(202)
        http=httpx.Client(transport=httpx.MockTransport(receiver))
        worker=WebhookWorker(db.conn,
            signing_master_key="s09-backlog-master-key-at-least-32-chars",
            allowed_hosts={"hooks.example.test"},http_client=http)
        assert worker.consume(limit=100,tenant_id=tenant)==64
        assert worker.consume(limit=100,tenant_id=tenant)==0
        assert len(seen)==len(set(seen))==64
        rows=db.conn.execute(
            """SELECT status,count(*) FROM memory_webhook_deliveries
               WHERE subscription_id=%s::uuid GROUP BY status""",(sub,)).fetchall()
        assert rows==[("delivered",64)]
    finally:
        db.close()
