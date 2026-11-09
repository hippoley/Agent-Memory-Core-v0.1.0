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
        assert OutboxWorker(db.conn).consume(
            event_type="memory.test.failure",handler=broken,limit=1)==0
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
def test_outbox_retry_exhaustion_dead_letter_and_operator_requeue():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.outbox import OutboxWorker
    db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        db.migrate()
        tenant="outbox-dlq-"+uuid4().hex
        event_id=uuid4()
        with db.conn.transaction():
            db.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'claim','sample','memory.test.dead-letter','{}'::jsonb)""",
                (event_id,tenant))
        worker=OutboxWorker(db.conn,max_attempts=1,
                            base_retry_seconds=0.01,max_retry_seconds=0.1)
        def broken(conn,tenant_id,delivery_id,aggregate_id,payload):
            conn.execute(
                """INSERT INTO memory_events(tenant_id,event_id,event_type,payload)
                   VALUES (%s,%s,'must.rollback','{}'::jsonb)""",
                (tenant_id,delivery_id))
            raise RuntimeError("permanent projection failure")
        assert worker.consume(event_type="memory.test.dead-letter",
                              handler=broken,limit=1)==0
        state=db.conn.execute(
            """SELECT attempts,processed_at,dead_lettered_at,last_attempt_at,last_error
               FROM memory_outbox WHERE event_id=%s""",(event_id,)).fetchone()
        assert state[0]==1 and state[1] is None and state[2] is not None
        assert state[3] is not None and "permanent projection failure" in state[4]
        assert worker.stats(event_type="memory.test.dead-letter") == {
            "pending":0,"processed":0,"dead_lettered":1,"total_attempts":1}
        assert db.conn.execute(
            "SELECT count(*) FROM memory_events WHERE tenant_id=%s",(tenant,)
        ).fetchone()[0]==0
        assert worker.consume(event_type="memory.test.dead-letter",
                              handler=broken,limit=1)==0
        assert worker.retry_dead_letter(event_id=str(event_id)) is True
        assert worker.retry_dead_letter(event_id=str(event_id)) is False
        state=db.conn.execute(
            "SELECT attempts,dead_lettered_at,last_error FROM memory_outbox WHERE event_id=%s",
            (event_id,)).fetchone()
        assert state==(0,None,None)
        assert worker.stats(event_type="memory.test.dead-letter")["pending"]==1
    finally:
        db.close()


@pytest.mark.integration
def test_projection_fanout_and_destination_independent_acknowledgement():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.projection_jobs import ProjectionWorker
    db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        db.migrate()
        tenant="projection-"+uuid4().hex
        event_id=uuid4()
        with db.conn.transaction():
            db.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'claim',%s,'memory.claim.created',
                           '{"memory_id":"projection-test"}'::jsonb)""",
                (event_id,tenant,str(event_id)))
        assert db.conn.execute(
            "SELECT count(*) FROM memory_projection_jobs WHERE event_id=%s",
            (event_id,)).fetchone()[0]==3
        worker=ProjectionWorker(db.conn,max_attempts=1,
                                base_retry_seconds=0.01,max_retry_seconds=0.1)
        def success(conn,tenant_id,delivery_id,aggregate_id,payload):
            conn.execute(
                """INSERT INTO memory_events(tenant_id,event_id,event_type,payload)
                   VALUES (%s,%s,'projection.fts.done','{}'::jsonb)
                   ON CONFLICT(tenant_id,event_id) DO NOTHING""",
                (tenant_id,delivery_id+":fts"))
        def fail(conn,tenant_id,delivery_id,aggregate_id,payload):
            raise RuntimeError("vector adapter unavailable")
        assert worker.consume(projection_name="fts",handler=success,limit=10,tenant_id=tenant)==1
        assert worker.consume(projection_name="vector",handler=fail,limit=10,tenant_id=tenant)==0
        states=dict(db.conn.execute(
            "SELECT projection_name,status FROM memory_projection_jobs WHERE event_id=%s",
            (event_id,)).fetchall())
        assert states=={"age":"pending","fts":"succeeded","vector":"dead_letter"}
        assert worker.stats(projection_name="vector",tenant_id=tenant)["dead_lettered"]==1
        assert worker.retry_dead_letter(event_id=str(event_id),
                                        projection_name="vector") is True
        states=dict(db.conn.execute(
            "SELECT projection_name,status FROM memory_projection_jobs WHERE event_id=%s",
            (event_id,)).fetchall())
        assert states["vector"]=="pending"
    finally:
        db.close()


@pytest.mark.integration
def test_fts_projection_indexes_chinese_and_is_tenant_scoped():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL required")
    from datetime import datetime, timezone
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.fts import project_claim_to_fts, search_fts
    db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        db.migrate()
        tenant="fts-"+uuid4().hex
        memory_id=db.remember(
            tenant_id=tenant,subject_id="卧室",predicate="空气质量偏好",
            value={"preference":"希望卧室保持安静，CO2低于800ppm"},
            source_id="conversation:fts-test",
            valid_from=datetime.now(timezone.utc))
        from agent_memory_core.projection_jobs import ProjectionWorker
        worker=ProjectionWorker(db.conn)
        assert worker.consume(projection_name="fts",tenant_id=tenant,
                              handler=project_claim_to_fts,limit=1)==1
        assert db.conn.execute(
            """SELECT aggregate_type,aggregate_id FROM memory_projection_jobs
               WHERE tenant_id=%s AND event_id IN (
                 SELECT event_id FROM memory_outbox
                 WHERE tenant_id=%s AND aggregate_id=%s
                   AND event_type='memory.claim.created')
                 AND projection_name='fts'""",
            (tenant,tenant,memory_id)).fetchone()==("claim",memory_id)
        event=db.conn.execute(
            """SELECT event_id,payload FROM memory_outbox
               WHERE tenant_id=%s AND aggregate_id=%s AND event_type='memory.claim.created'""",
            (tenant,memory_id)).fetchone()
        hits=search_fts(db.conn,tenant_id=tenant,query="卧室保持安静")
        assert hits and hits[0]["memory_id"]==memory_id
        assert search_fts(db.conn,tenant_id="other-"+tenant,
                          query="卧室保持安静")==[]
        # Replaying the same projection is idempotent.
        project_claim_to_fts(db.conn,tenant,str(event[0]),memory_id,event[1])
        assert db.conn.execute(
            "SELECT count(*) FROM memory_fts_documents WHERE tenant_id=%s",
            (tenant,)).fetchone()[0]==1
    finally:
        db.close()
