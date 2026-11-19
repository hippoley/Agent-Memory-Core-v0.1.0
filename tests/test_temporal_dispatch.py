"""PostgreSQL integration tests for durable Temporal dispatch claims."""
import os
from uuid import uuid4
import pytest


@pytest.mark.integration
def test_temporal_dispatch_identity_is_stable_and_claim_is_fenced():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.temporal_dispatch import TemporalDispatchStore,stable_workflow_id
    db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    tenant="temporal-dispatch-"+uuid4().hex
    task_id=str(uuid4())
    task_queue="dispatch-test-"+uuid4().hex
    try:
        db.migrate()
        with db.conn.transaction():
            db.conn.execute(
                """INSERT INTO memory_durable_tasks(task_id,tenant_id,task_type,input)
                   VALUES (%s::uuid,%s,'dispatch-test','{}'::jsonb)""",(task_id,tenant))
        workflow_id=stable_workflow_id(tenant,task_id)
        store=TemporalDispatchStore(db.conn)
        first=store.enqueue(tenant_id=tenant,task_id=task_id,workflow_id=workflow_id,task_queue=task_queue)
        second=store.enqueue(tenant_id=tenant,task_id=task_id,workflow_id=workflow_id,task_queue=task_queue)
        assert first["workflow_id"]==second["workflow_id"]==workflow_id
        a=store.claim(dispatcher_id="dispatcher-a",lease_seconds=30,task_queue=task_queue)
        assert a and a["task_id"]==task_id and a["lease_epoch"]==1
        # A competing dispatcher cannot steal a live lease.
        assert store.claim(dispatcher_id="dispatcher-b",lease_seconds=30,task_queue=task_queue) is None
        # A stale owner cannot acknowledge another dispatcher's lease.
        assert not store.mark_started(task_id=task_id,dispatcher_id="dispatcher-b",
                                      lease_epoch=1,temporal_run_id="run-stale")
        assert store.mark_started(task_id=task_id,dispatcher_id="dispatcher-a",
                                   lease_epoch=1,temporal_run_id="run-1")
        assert store.claim(dispatcher_id="dispatcher-b",task_queue=task_queue) is None
        state=db.conn.execute(
            """SELECT status,workflow_id,temporal_run_id,lease_owner
               FROM memory_temporal_dispatches WHERE task_id=%s::uuid""",(task_id,)).fetchone()
        assert state==("started",workflow_id,"run-1",None)
    finally:
        db.close()


@pytest.mark.integration
def test_expired_temporal_dispatch_lease_can_be_reclaimed_with_new_epoch():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.temporal_dispatch import TemporalDispatchStore,stable_workflow_id
    db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    tenant="temporal-dispatch-expiry-"+uuid4().hex
    task_id=str(uuid4())
    task_queue="dispatch-expiry-"+uuid4().hex
    try:
        db.migrate()
        with db.conn.transaction():
            db.conn.execute(
                """INSERT INTO memory_durable_tasks(task_id,tenant_id,task_type,input)
                   VALUES (%s::uuid,%s,'dispatch-test','{}'::jsonb)""",(task_id,tenant))
        store=TemporalDispatchStore(db.conn)
        store.enqueue(tenant_id=tenant,task_id=task_id,
                      workflow_id=stable_workflow_id(tenant,task_id),task_queue=task_queue)
        first=store.claim(dispatcher_id="dead-dispatcher",lease_seconds=1,task_queue=task_queue)
        db.conn.execute(
            "UPDATE memory_temporal_dispatches SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE task_id=%s::uuid",
            (task_id,))
        db.conn.commit()
        second=store.claim(dispatcher_id="recovery-dispatcher",lease_seconds=30,task_queue=task_queue)
        assert second and second["lease_epoch"]==first["lease_epoch"]+1
        before_stale_ack=db.conn.execute(
            """SELECT status,lease_owner,lease_epoch,temporal_run_id
               FROM memory_temporal_dispatches WHERE task_id=%s::uuid""",(task_id,)).fetchone()
        assert before_stale_ack==("dispatching","recovery-dispatcher",second["lease_epoch"],None)
        assert not store.mark_started(task_id=task_id,dispatcher_id="dead-dispatcher",
                                      lease_epoch=first["lease_epoch"],temporal_run_id="stale")
        after_stale_ack=db.conn.execute(
            """SELECT status,lease_owner,lease_epoch,temporal_run_id
               FROM memory_temporal_dispatches WHERE task_id=%s::uuid""",(task_id,)).fetchone()
        assert after_stale_ack==before_stale_ack
        assert store.mark_started(task_id=task_id,dispatcher_id="recovery-dispatcher",
                                  lease_epoch=second["lease_epoch"],temporal_run_id="run-recovered")
    finally:
        db.close()

@pytest.mark.integration
def test_two_independent_dispatchers_racing_claim_exactly_one_lease():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL required")
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.temporal_dispatch import TemporalDispatchStore,stable_workflow_id

    dsn=os.environ["MEMORY_TEST_DSN"]
    tenant="temporal-dispatch-race-"+uuid4().hex
    task_id=str(uuid4())
    task_queue="dispatch-race-"+uuid4().hex
    setup=PostgresMemory(dsn)
    try:
        setup.migrate()
        with setup.conn.transaction():
            setup.conn.execute(
                """INSERT INTO memory_durable_tasks(task_id,tenant_id,task_type,input)
                   VALUES (%s::uuid,%s,'dispatch-race','{}'::jsonb)""",(task_id,tenant))
        TemporalDispatchStore(setup.conn).enqueue(
            tenant_id=tenant,task_id=task_id,
            workflow_id=stable_workflow_id(tenant,task_id),task_queue=task_queue)

        barrier=Barrier(2)
        def contender(dispatcher_id):
            db=PostgresMemory(dsn)
            try:
                barrier.wait(timeout=10)
                claim=TemporalDispatchStore(db.conn).claim(
                    dispatcher_id=dispatcher_id,lease_seconds=30,task_queue=task_queue)
                return dispatcher_id,claim
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(contender,("dispatcher-race-a","dispatcher-race-b")))

        winners=[item for item in results if item[1] is not None]
        losers=[item for item in results if item[1] is None]
        assert len(winners)==1
        assert len(losers)==1
        winner_id,winner_claim=winners[0]
        assert winner_claim["task_id"]==task_id
        assert winner_claim["lease_epoch"]==1
        state=setup.conn.execute(
            """SELECT status,lease_owner,lease_epoch,attempts
               FROM memory_temporal_dispatches WHERE task_id=%s::uuid""",(task_id,)).fetchone()
        assert state==("dispatching",winner_id,1,1)
        assert winner_claim["lease_epoch"]==state[2]
    finally:
        setup.close()
