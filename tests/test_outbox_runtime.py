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
def test_projection_fanout_independent_receipts_and_dead_letter_replay():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.outbox import ProjectionOutboxWorker
    db = PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        db.migrate()
        tenant = "fanout-" + uuid4().hex
        eid = uuid4()
        with db.conn.transaction():
            db.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'claim','sample','memory.test.fanout','{}'::jsonb)""",
                (eid,tenant))
        worker = ProjectionOutboxWorker(db.conn)
        def receipt(conn, tenant_id, event_id, aggregate, payload):
            conn.execute(
                """INSERT INTO memory_events(tenant_id,event_id,event_type,payload)
                   VALUES (%s,%s,'fanout.received','{}'::jsonb)
                   ON CONFLICT(tenant_id,event_id) DO NOTHING""",
                (tenant_id,event_id))
        assert worker.consume(consumer_id="vector",event_type="memory.test.fanout",
                              handler=receipt) >= 1
        assert worker.consume(consumer_id="vector",event_type="memory.test.fanout",
                              handler=receipt) == 0
        # Separate consumer can receive the same original event independently.
        assert worker.consume(consumer_id="graph",event_type="memory.test.fanout",
                              handler=lambda *args: None) >= 1
        rows = db.conn.execute(
            """SELECT consumer_id,delivered_at IS NOT NULL
               FROM memory_outbox_deliveries WHERE event_id=%s
               ORDER BY consumer_id""", (eid,)).fetchall()
        assert rows == [("graph",True),("vector",True)]
        def broken(conn, tenant_id, event_id, aggregate, payload):
            conn.execute(
                """INSERT INTO memory_events(tenant_id,event_id,event_type,payload)
                   VALUES (%s,%s,'should.roll.back','{}'::jsonb)""",
                (tenant_id,event_id+"-broken"))
            raise RuntimeError("projection offline")
        assert worker.consume(consumer_id="fts",event_type="memory.test.fanout",
                              handler=broken,max_attempts=1) == 0
        dlq = db.conn.execute(
            """SELECT attempts,dead_letter_at IS NOT NULL,delivered_at
               FROM memory_outbox_deliveries WHERE event_id=%s AND consumer_id='fts'""",
            (eid,)).fetchone()
        assert dlq == (1, True, None)
        assert db.conn.execute(
            "SELECT count(*) FROM memory_events WHERE tenant_id=%s AND event_type='should.roll.back'",
            (tenant,)).fetchone()[0] == 0
        assert worker.retry_dead_letter(tenant_id=tenant,consumer_id="fts",
                                        event_id=str(eid))
        assert not worker.retry_dead_letter(tenant_id="foreign-"+tenant,
                                            consumer_id="fts",event_id=str(eid))
        assert worker.consume(consumer_id="fts",event_type="memory.test.fanout",
                              handler=lambda *args: None) >= 1
        metrics = worker.status(consumer_id="fts",tenant_id=tenant)
        assert metrics["delivered"] == 1
        assert metrics["pending"] == 0
        assert metrics["dead_letter"] == 0
        assert metrics["oldest_pending_seconds"] is None
        assert worker.status(consumer_id="fts",tenant_id="foreign-"+tenant)["delivered"] == 0
        assert db.conn.execute(
            """SELECT delivered_at IS NOT NULL FROM memory_outbox_deliveries
               WHERE event_id=%s AND consumer_id='fts'""",(eid,)).fetchone()[0]
    finally:
        db.close()


@pytest.mark.integration
def test_fts_projector_rebuildable_tenant_scoped_and_bitemporal():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL required")
    from datetime import datetime, timezone
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.outbox import ProjectionOutboxWorker, project_claim_fts
    db = PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    tenant = "fts-" + uuid4().hex
    other = "other-" + tenant
    word = "zenithpomegranate" + uuid4().hex[:12]
    try:
        db.migrate()
        mid = db.remember(tenant_id=tenant, subject_id="room",
            predicate="fact", value={"text":word}, source_id="speech",
            valid_from=datetime(2026, 11, 1, tzinfo=timezone.utc))
        worker=ProjectionOutboxWorker(db.conn)
        assert db.fts_search(tenant_id=tenant,query=word)==[]
        # A bounded batch may first contain older events in a shared DB.
        for _ in range(30):
            worker.consume(consumer_id="fts-test-v1",
                           event_type="memory.claim.created",
                           handler=project_claim_fts,limit=100)
            if db.fts_search(tenant_id=tenant,query=word):
                break
        assert db.fts_search(tenant_id=tenant,query=word)==[mid]
        from agent_memory_core.hybrid import HybridMemorySearch
        report = HybridMemorySearch(db).search_detailed(
            tenant_id=tenant, query=word, limit=10)
        assert report.hits and report.hits[0].memory_id == mid
        assert "fts" in report.hits[0].channels
        assert "fts" in report.channels_used
        assert db.fts_search(tenant_id=other,query=word)==[]
        assert db.fts_search(tenant_id=tenant,query=word,
               valid_at=datetime(2026,10,1,tzinfo=timezone.utc))==[]
        # Superseded claims must disappear from current retrieval, but remain
        # visible to correct historical as-of queries.
        past_known = db.current_time()
        db.remember(tenant_id=tenant, subject_id="room", predicate="fact",
                    value={"text":"revised"}, source_id="correction",
                    valid_from=datetime(2026,11,1,tzinfo=timezone.utc),
                    replaces_id=mid)
        assert db.fts_search(tenant_id=tenant,query=word)==[]
        assert db.fts_search(tenant_id=tenant,query=word,
              valid_at=datetime(2026,11,2,tzinfo=timezone.utc),
              known_at=past_known)==[mid]
        # A second connection sees the same persisted projection.
        reopened=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
        try:
            assert reopened.fts_search(tenant_id=tenant,query=word,
                 valid_at=datetime(2026,11,2,tzinfo=timezone.utc),
                 known_at=past_known)==[mid]
        finally:
            reopened.close()
        assert db.conn.execute(
            "SELECT count(*) FROM memory_outbox_deliveries WHERE "
            "event_id=%s AND consumer_id='fts-test-v1' AND delivered_at IS NOT NULL",
            (mid,)).fetchone()[0]==1
    finally:
        db.close()


@pytest.mark.integration
def test_age_outbox_auto_projects_retires_and_reconciles_sourced_edges():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real AGE/PostgreSQL required")
    from datetime import datetime, timezone
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.age_graph import AgeProjection, project_age_from_outbox
    from agent_memory_core.outbox import ProjectionOutboxWorker
    tenant = "age-outbox-" + uuid4().hex
    db = PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        db.migrate()
        for entity_id, kind in (("room","room"),("window-a","device"),
                                ("window-b","device")):
            db.upsert_entity(tenant_id=tenant, entity_id=entity_id,
                entity_type=kind, canonical_name=entity_id)
        mid=db.remember(tenant_id=tenant,subject_id="room",
            predicate="status",value={"mode":"old"},source_id="source",
            valid_from=datetime(2026,11,1,tzinfo=timezone.utc))
        edge_a=db.add_edge(tenant_id=tenant,from_entity="room",
            predicate="HAS_WINDOW",to_entity="window-a",source_memory_id=mid)
        edge_b=db.add_edge(tenant_id=tenant,from_entity="room",
            predicate="HAS_WINDOW",to_entity="window-b")
        graph=AgeProjection(db.conn)
        graph.initialize()
        worker=ProjectionOutboxWorker(db.conn)
        assert graph.neighbors(tenant_id=tenant,from_entity="room")==[]
        assert worker.consume(consumer_id="age-test-v1",event_type="memory.edge.created",
              handler=project_age_from_outbox,limit=20)>=2
        assert set(graph.neighbors(tenant_id=tenant,from_entity="room")) == {
            "window-a","window-b"}
        assert worker.consume(consumer_id="age-test-v1",event_type="memory.edge.created",
              handler=project_age_from_outbox,limit=20)==0
        assert db.retire_edge(tenant_id=tenant,edge_id=edge_b)
        assert worker.consume(consumer_id="age-test-v1",event_type="memory.edge.retired",
              handler=project_age_from_outbox,limit=20)==1
        assert graph.neighbors(tenant_id=tenant,from_entity="room")==["window-a"]
        db.remember(tenant_id=tenant,subject_id="room",predicate="status",
            value={"mode":"new"},source_id="correction",
            valid_from=datetime(2026,11,1,tzinfo=timezone.utc),replaces_id=mid)
        assert worker.consume(consumer_id="age-test-v1",event_type="memory.claim.created",
              handler=project_age_from_outbox,limit=20)>=1
        assert graph.neighbors(tenant_id=tenant,from_entity="room")==[]
        assert db.conn.execute(
            "SELECT count(*) FROM memory_graph_projection WHERE edge_id IN (%s,%s)",
            (edge_a,edge_b)).fetchone()[0]==0
        assert worker.status(consumer_id="age-test-v1",tenant_id=tenant)["dead_letter"]==0
    finally:
        db.close()
