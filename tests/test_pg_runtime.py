"""Integration tests require the real co-installed PostgreSQL/pgvector/AGE stack.
Enable with MEMORY_TEST_DSN. Never report these tests as passing when skipped.
"""
from datetime import datetime, timezone
from uuid import uuid4
import os
import pytest

pytestmark = pytest.mark.integration

DSN = os.getenv("MEMORY_TEST_DSN")
if not DSN:
    pytest.skip("MEMORY_TEST_DSN not set; triple-store integration not exercised", allow_module_level=True)

from agent_memory_core.postgres import PostgresMemory
from agent_memory_core.age_graph import AgeProjection
from agent_memory_core.hybrid import HybridMemorySearch


def test_real_postgres_pgvector_age():
    unique = uuid4().hex
    tenant = "test-" + unique
    db = PostgresMemory(DSN)
    try:
        db.migrate()
        mid = db.remember(
            tenant_id=tenant, subject_id="room-1", predicate="preference",
            value={"text": "quiet nights"}, source_id="user-stated",
            valid_from=datetime(2026,11,1,tzinfo=timezone.utc),
        )
        rows = db.recall(tenant_id=tenant, subject_id="room-1")
        assert len(rows) == 1 and rows[0]["id"] == mid
        claim_events = db.events_after(tenant_id=tenant, limit=10)
        assert any(e["event_id"] == mid and e["event_type"] == "memory.claim.created"
                   for e in claim_events)
        claim_cursor = next(e["event_id"] for e in claim_events
                            if e["event_id"] == mid)
        assert db.events_after(tenant_id=tenant, after=claim_cursor, limit=10) == []
        assert db.recall(tenant_id="not-"+tenant,subject_id="room-1")==[]
        vec=[0.0]*384
        vec[0]=1.0
        db.store_embedding(tenant_id=tenant,memory_id=mid,model_id="test384",vector=vec)
        # Simulate upgrade from a pre-registry 384-dimension database.
        db.conn.execute(
            "DELETE FROM memory_embedding_models WHERE tenant_id=%s AND model_id=%s",
            (tenant,"test384"))
        db.migrate()
        assert db.vector_search(tenant_id=tenant,model_id="test384",vector=vec)[0][0]==mid

        with pytest.raises(PermissionError):
            db.store_embedding(tenant_id="wrong-"+tenant,memory_id=mid,model_id="test384",vector=vec)
        assert db.vector_search(tenant_id=tenant,model_id="test384",vector=vec)[0][0]==mid
        # 512-dimensional BGE-small-zh-v1.5 profile compatibility (fixture only).
        vec512=[0.0]*512
        vec512[0]=1.0
        db.store_embedding(tenant_id=tenant,memory_id=mid,
                           model_id="BAAI/bge-small-zh-v1.5",vector=vec512)
        assert db.vector_search(tenant_id=tenant,
            model_id="BAAI/bge-small-zh-v1.5",vector=vec512)[0][0]==mid
        with pytest.raises(ValueError):
            db.vector_search(tenant_id=tenant,
                model_id="BAAI/bge-small-zh-v1.5",vector=vec)
        with pytest.raises(ValueError):
            db.store_embedding(tenant_id=tenant,memory_id=mid,
                model_id="BAAI/bge-small-zh-v1.5",vector=vec)


        db.add_edge(tenant_id=tenant,from_entity="room-1",predicate="HAS_WINDOW",to_entity="window-1",
                    source_memory_id=mid)
        with pytest.raises(PermissionError):
            db.add_edge(tenant_id="wrong-"+tenant,from_entity="window-1",predicate="BELONGS_TO",to_entity="home-1",source_memory_id=mid)
        db.add_edge(tenant_id=tenant,from_entity="window-1",predicate="BELONGS_TO",to_entity="home-1",
                    source_memory_id=mid)
        age=AgeProjection(db.conn)
        age.initialize()
        assert age.project_pending(tenant_id=tenant).projected==2
        assert age.project_pending(tenant_id=tenant).projected==0
        assert "window-1" in age.neighbors(tenant_id=tenant,from_entity="room-1")
        assert "home-1" in age.neighbors(tenant_id=tenant,from_entity="room-1",hops=2)
        assert age.neighbors(tenant_id="not-"+tenant,from_entity="room-1")==[]
        # Late-arriving observations must be sorted by physical event time,
        # not the order in which PostgreSQL received them.
        later=datetime(2026,11,7,12,30,tzinfo=timezone.utc)
        earlier=datetime(2026,11,7,12,0,tzinfo=timezone.utc)
        assert db.record_observation(
            tenant_id=tenant, device_id="window-1", source_event_id="evt-later",
            observed_at=later,property="angle",value=35.0,unit="degree",
            source_id="gateway-1")
        assert db.record_observation(
            tenant_id=tenant, device_id="window-1", source_event_id="evt-earlier",
            observed_at=earlier,property="angle",value=17.5,unit="degree",
            source_id="gateway-1")
        assert not db.record_observation(
            tenant_id=tenant, device_id="window-1", source_event_id="evt-later",
            observed_at=later,property="angle",value=35.0,unit="degree",
            source_id="gateway-1")
        with pytest.raises(ValueError):
            db.record_observation(
                tenant_id=tenant, device_id="window-1", source_event_id="evt-later",
                observed_at=later,property="angle",value=70.0,unit="degree",
                source_id="gateway-1")
        observations=db.observations(
            tenant_id=tenant,device_id="window-1",property="angle")
        assert [o["source_event_id"] for o in observations] == ["evt-later","evt-earlier"]
        observation_events = db.events_after(tenant_id=tenant, limit=20)
        assert any(e["event_type"] == "memory.observation.recorded"
                   and e["payload"]["source_event_id"] == "evt-later"
                   for e in observation_events)
        assert db.observations(
            tenant_id="not-"+tenant,device_id="window-1",property="angle")==[]


        db.upsert_entity(tenant_id=tenant,entity_id='canonical-bedroom',entity_type='room',canonical_name='Main Bedroom',aliases=['primary bedroom'])
        resolved = db.resolve_entity(tenant_id=tenant,name='PRIMARY BEDROOM')
        assert resolved and resolved['entity_id']=='canonical-bedroom'
        from agent_memory_core.processing import MemoryProcessor
        processor = MemoryProcessor(db,entity_resolver=lambda t,n:db.resolve_entity(tenant_id=t,name=n))
        extraction = lambda _: [{"subject_id":"processor-room",
            "predicate":"quiet_hours","value":{"start":"22:00","end":"07:00"},
            "confidence":0.97}]
        first = processor.process(tenant_id=tenant,text="User prefers quiet hours",
                                  source_id="conversation-a",extractor=extraction)
        second = processor.process(tenant_id=tenant,text="User repeats preference",
                                   source_id="conversation-b",extractor=extraction)
        assert len(first.created)==1 and second.duplicates==1
        changed = processor.process(tenant_id=tenant,text="User updates preference",
            source_id="conversation-c",
            extractor=lambda _: [{"subject_id":"processor-room",
                "predicate":"quiet_hours","value":{"start":"23:00","end":"07:00"}}])
        assert len(changed.created)==1 and len(changed.replaced)==1

        hybrid=HybridMemorySearch(db, age)
        hits=hybrid.search(
            tenant_id=tenant,query="quiet nights",embedding=vec,
            model_id="test384",anchor_entity="room-1",
        )
        assert hits and hits[0].memory_id==mid
        assert set(hits[0].channels)=={"sql","vector","graph"}
        assert hybrid.search(
            tenant_id="not-"+tenant,query="quiet nights",
            embedding=vec,model_id="test384",anchor_entity="room-1",
        ) == []
        special='window-"\\\\b'
        db.add_edge(tenant_id=tenant,from_entity="room-1",predicate="SPECIALLY_NAMED",to_entity=special,
                    source_memory_id=mid)
        assert age.project_pending(tenant_id=tenant).projected==1
        assert special in age.neighbors(tenant_id=tenant,from_entity="room-1")

        # Capture database time before correction to verify historical vector recall.
        past_known_at=db.conn.execute("SELECT clock_timestamp()").fetchone()[0]
        # Superseding the sourced claim invalidates graph relationships.
        new_mid=db.remember(tenant_id=tenant,subject_id="room-1",predicate="preference",
                    value={"text":"prefer ventilation"},source_id="user-correction",
                    valid_from=datetime(2026,11,8,tzinfo=timezone.utc),
                    replaces_id=mid)
        old_change_time=db.conn.execute(
            "SELECT superseded_at FROM memory_claims WHERE id=%s",(mid,)).fetchone()[0]
        new_record_time=db.conn.execute(
            "SELECT recorded_at FROM memory_claims WHERE id=%s",(new_mid,)).fetchone()[0]
        assert old_change_time==new_record_time
        assert db.vector_search(tenant_id=tenant,model_id="test384",vector=vec) == []
        assert db.vector_search(
            tenant_id=tenant,model_id="test384",vector=vec,
            valid_at=datetime(2026,11,7,tzinfo=timezone.utc),known_at=past_known_at
        )[0][0]==mid
        assert age.reconcile_invalidated(tenant_id=tenant) == 3
        assert age.neighbors(tenant_id=tenant,from_entity="room-1") == []
        assert age.project_pending(tenant_id=tenant).projected == 0

        # Explicit retirement is also a monotonic, repeat-safe operation.
        new_edge=db.add_edge(tenant_id=tenant,from_entity="room-1",
                             predicate="WINDOW",to_entity="window-current")
        assert age.project_pending(tenant_id=tenant).projected == 1
        assert "window-current" in age.neighbors(tenant_id=tenant,from_entity="room-1")
        assert db.retire_edge(tenant_id=tenant,edge_id=new_edge)
        assert not db.retire_edge(tenant_id=tenant,edge_id=new_edge)
        assert age.reconcile_invalidated(tenant_id=tenant) == 1
        assert "window-current" not in age.neighbors(tenant_id=tenant,from_entity="room-1")
    finally:
        db.close()
