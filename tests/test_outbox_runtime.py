"""Real PostgreSQL outbox delivery/acknowledgement and retry tests."""
import os
from uuid import uuid4
import pytest

@pytest.mark.integration
def test_outbox_receipt_consumed_atomically():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.outbox import OutboxWorker,persist_verification_receipt
    db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        db.migrate()
        tenant="outbox-"+uuid4().hex
        e=uuid4()
        with db.conn.transaction():
            db.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'claim','sample','memory.claim.verified',
                           '{"active":true}'::jsonb)""",(e,tenant))
        worker=OutboxWorker(db.conn)
        # Process all pending receipts, including test-run leftovers. Handler is
        # transactionally idempotent; the test asserts its own tenant's records.
        assert worker.consume(event_type="memory.claim.verified",
                              handler=persist_verification_receipt,limit=100)>=1
        assert db.conn.execute(
            "SELECT count(*) FROM memory_events WHERE tenant_id=%s AND event_id=%s",
            (tenant,str(e))).fetchone()[0]==1
        assert db.conn.execute(
            "SELECT processed_at IS NOT NULL FROM memory_outbox WHERE event_id=%s",(e,)
        ).fetchone()[0]
        assert worker.consume(event_type="memory.claim.verified",
                              handler=persist_verification_receipt,limit=100)==0
        assert db.conn.execute(
            "SELECT count(*) FROM memory_events WHERE tenant_id=%s AND event_id=%s",
            (tenant,str(e))).fetchone()[0]==1
    finally:
        db.close()

@pytest.mark.integration
def test_outbox_failed_handler_is_retryable_not_acknowledged():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.outbox import OutboxWorker
    db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        db.migrate()
        tenant="outbox-error-"+uuid4().hex
        e=uuid4()
        with db.conn.transaction():
            db.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'claim','sample','memory.test.failure','{}'::jsonb)""",
                (e,tenant))
        def broken(conn,tenant,event_id,aggregate,payload):
            conn.execute("INSERT INTO memory_events(tenant_id,event_id,event_type,payload) VALUES (%s,%s,'temporary','{}'::jsonb)",(tenant,event_id))
            raise RuntimeError("deliberate fault")
        # Tenant-scoped claim: leftover events from earlier runs of this test
        # on a shared database must not consume this run's attempt budget.
        assert OutboxWorker(db.conn).consume(
            event_type="memory.test.failure",handler=broken,limit=1,
            tenant_id=tenant)==0
        state=db.conn.execute(
            "SELECT attempts,processed_at,last_error FROM memory_outbox WHERE event_id=%s",
            (e,)).fetchone()
        assert state[0]==1 and state[1] is None and "deliberate fault" in state[2]
        assert db.conn.execute(
            "SELECT count(*) FROM memory_events WHERE tenant_id=%s",(tenant,)
        ).fetchone()[0]==0
    finally:
        db.close()


@pytest.mark.integration
def test_outbox_retries_to_dead_letter_and_operator_replay():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.outbox import OutboxWorker,persist_verification_receipt
    db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        db.migrate()
        tenant="outbox-dlq-"+uuid4().hex
        event_id=uuid4()
        with db.conn.transaction():
            db.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'claim','sample','memory.test.dead','{}'::jsonb)""",
                (event_id,tenant))
        worker=OutboxWorker(db.conn,max_attempts=2)
        def broken(conn,tenant_id,event,aggregate,payload):
            conn.execute(
                """INSERT INTO memory_events(tenant_id,event_id,event_type,payload)
                   VALUES (%s,%s,'must.rollback','{}'::jsonb)""",(tenant_id,event))
            raise RuntimeError("injected handler crash")
        assert worker.consume(event_type="memory.test.dead",handler=broken,
                              limit=1,tenant_id=tenant)==0
        db.conn.execute(
            "UPDATE memory_outbox SET available_at=clock_timestamp() WHERE event_id=%s",
            (event_id,))
        db.conn.commit()
        assert worker.consume(event_type="memory.test.dead",handler=broken,
                              limit=1,tenant_id=tenant)==0
        state=db.conn.execute(
            """SELECT attempts,processed_at,dead_lettered_at,last_error
               FROM memory_outbox WHERE event_id=%s""",(event_id,)).fetchone()
        assert state[0]==2 and state[1] is None and state[2] is not None
        assert "injected handler crash" in state[3]
        assert db.conn.execute(
            "SELECT count(*) FROM memory_events WHERE tenant_id=%s AND event_type='must.rollback'",
            (tenant,)).fetchone()[0]==0
        assert worker.consume(event_type="memory.test.dead",handler=broken,
                              limit=1,tenant_id=tenant)==0
        assert worker.requeue_dead(tenant_id=tenant,event_id=str(event_id))
        assert worker.consume(event_type="memory.test.dead",
            handler=persist_verification_receipt,limit=1,tenant_id=tenant)==1
        final=db.conn.execute(
            """SELECT attempts,processed_at,dead_lettered_at FROM memory_outbox
               WHERE event_id=%s""",(event_id,)).fetchone()
        assert final[0]==1 and final[1] is not None and final[2] is None
    finally:
        db.close()


@pytest.mark.integration
def test_outbox_process_sigkill_rolls_back_claim_and_recovers():
    """A killed consumer cannot strand a row lock or partially commit its effect."""
    import subprocess
    import sys
    import time

    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.outbox import OutboxWorker, persist_verification_receipt

    db = PostgresMemory(dsn)
    tenant = "outbox-sigkill-" + uuid4().hex
    event_id = uuid4()
    child = None
    try:
        db.migrate()
        with db.conn.transaction():
            db.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'claim','sigkill-sample',
                           'memory.claim.verified','{"source":"sigkill"}'::jsonb)""",
                (event_id, tenant),
            )

        # The child owns a real PostgreSQL transaction and row lock. It writes
        # the downstream event inside that same transaction, then blocks before
        # acknowledging the outbox row. SIGKILL must roll back both operations.
        child_code = r'''
import json, os, time
import psycopg
from agent_memory_core.outbox import OutboxWorker

conn = psycopg.connect(os.environ["MEMORY_TEST_DSN"])
def blocking_handler(conn, tenant_id, event_id, aggregate_id, payload):
    conn.execute(
        """INSERT INTO memory_events(tenant_id,event_id,event_type,payload)
           VALUES (%s,%s,'memory.claim.verified',%s::jsonb)
           ON CONFLICT(tenant_id,event_id) DO NOTHING""",
        (tenant_id, event_id, json.dumps(payload)),
    )
    print("WORKER_LOCKED_WITH_UNCOMMITTED_EFFECT", flush=True)
    time.sleep(120)

OutboxWorker(conn).consume(
    event_type="memory.claim.verified",
    handler=blocking_handler,
    limit=1,
    tenant_id=os.environ["OUTBOX_TENANT_ID"],
)
'''
        env = dict(os.environ)
        env["OUTBOX_EVENT_ID"] = str(event_id)
        env["OUTBOX_TENANT_ID"] = tenant
        child = subprocess.Popen(
            [sys.executable, "-c", child_code],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        ready = child.stdout.readline().strip()
        assert ready == "WORKER_LOCKED_WITH_UNCOMMITTED_EFFECT", (
            f"Child failed before acquiring the transaction: {ready!r}; "
            f"stderr={child.stderr.read() if child.poll() is not None else ''}"
        )

        child.kill()
        child.wait(timeout=10)
        child = None

        # PostgreSQL must release the dead process's lock and roll back the
        # uncommitted downstream effect, leaving the original outbox event ready.
        state = db.conn.execute(
            """SELECT processed_at, attempts FROM memory_outbox
               WHERE event_id=%s""",
            (str(event_id),),
        ).fetchone()
        assert state == (None, 0)
        assert db.conn.execute(
            """SELECT count(*) FROM memory_events
               WHERE tenant_id=%s AND event_id=%s""",
            (tenant, str(event_id)),
        ).fetchone()[0] == 0
        db.conn.commit()

        # A fresh consumer process/connection can now claim and commit it once.
        worker = OutboxWorker(db.conn)
        assert worker.consume(
            event_type="memory.claim.verified",
            handler=persist_verification_receipt,
            limit=1,
            tenant_id=tenant,
        ) == 1
        final = db.conn.execute(
            """SELECT processed_at IS NOT NULL, attempts
               FROM memory_outbox WHERE event_id=%s""",
            (event_id,),
        ).fetchone()
        assert final == (True, 1)
        assert db.conn.execute(
            """SELECT count(*) FROM memory_events
               WHERE tenant_id=%s AND event_id=%s""",
            (tenant, str(event_id)),
        ).fetchone()[0] == 1
    finally:
        if child is not None:
            child.kill()
            child.wait(timeout=10)
        db.close()
