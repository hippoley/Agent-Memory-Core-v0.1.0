"""Trusted predicate cardinality: one vs many is a database policy, not model output."""
from datetime import datetime,timezone
import os
from uuid import uuid4

import pytest
from agent_memory_core.processing import MemoryProcessor


class FakeStore:
    def __init__(self):
        self.rows=[]
        self.sources=[]
        self.policies={}
    def predicate_cardinality(self,*,tenant_id,predicate):
        return self.policies.get((tenant_id,predicate),"one")
    def set_predicate_cardinality(self,*,tenant_id,predicate,cardinality):
        self.policies[(tenant_id,predicate)]=cardinality
    def recall(self,*,tenant_id,subject_id,predicate,valid_at,known_at):
        return [r for r in reversed(self.rows) if r["tenant_id"]==tenant_id
                and r["subject_id"]==subject_id and r["predicate"]==predicate
                and r["superseded_at"] is None]
    def remember(self,**kw):
        old=kw.get("replaces_id")
        if old:
            for row in self.rows:
                if row["id"]==old:
                    row["superseded_at"]=datetime.now(timezone.utc)
        mid=f"m-{len(self.rows)+1}"
        self.rows.append({"id":mid,"tenant_id":kw["tenant_id"],
            "subject_id":kw["subject_id"],"predicate":kw["predicate"],
            "value":kw["value"],"superseded_at":None})
        return mid
    def link_claim_source(self,**kw):
        self.sources.append(kw)
        return True


def test_many_valued_relations_coexist_and_exact_duplicates_link_provenance():
    store=FakeStore()
    store.set_predicate_cardinality(tenant_id="home",predicate="HAS_WINDOW",cardinality="many")
    processor=MemoryProcessor(store)
    candidates=[
        {"subject_id":"living-room","predicate":"HAS_WINDOW","value":{"target_id":"window-a"}},
        {"subject_id":"living-room","predicate":"HAS_WINDOW","value":{"target_id":"window-b"}},
        {"subject_id":"living-room","predicate":"HAS_WINDOW","value":{"target_id":"window-a"}},
    ]
    report=processor.process(tenant_id="home",text="two windows",source_id="dialogue",
                             extractor=lambda _:candidates)
    assert len(report.created)==2
    assert report.duplicates==1
    assert report.replaced==[]
    assert {r["value"]["target_id"] for r in store.rows}=={"window-a","window-b"}
    assert len(store.sources)==1


def test_extractor_cannot_upgrade_cardinality_or_override_entity_identity():
    store=FakeStore()
    processor=MemoryProcessor(store,entity_resolver=lambda tenant,name:
        {"entity_id":"canonical-bedroom"} if name=="卧室" else None)
    blocked=processor.process(tenant_id="home",text="window",source_id="s1",
        extractor=lambda _:[{"subject_id":"room","predicate":"HAS_WINDOW",
            "value":{"target_id":"window-a"},"cardinality":"many"}])
    assert blocked.created==[]
    assert blocked.ambiguous==1 and blocked.rejected==1
    mismatch=processor.process(tenant_id="home",text="bedroom",source_id="s2",
        extractor=lambda _:[{"subject_id":"attacker-controlled-id","subject_name":"卧室",
            "predicate":"preference","value":{"x":1}}])
    assert mismatch.created==[]
    assert mismatch.ambiguous==1
    unknown=processor.process(tenant_id="home",text="unknown",source_id="s3",
        extractor=lambda _:[{"subject_id":"guessed-id","subject_name":"未知房间",
            "predicate":"preference","value":{"x":1}}])
    assert unknown.created==[]
    assert unknown.ambiguous==1


def test_unresolved_subject_name_without_resolver_is_rejected():
    processor=MemoryProcessor(FakeStore())
    report=processor.process(tenant_id="home",text="unknown",source_id="s1",
        extractor=lambda _:[{"subject_name":"某个房间","predicate":"noise","value":{"level":"quiet"}}])
    assert report.created==[]
    assert report.ambiguous==1 and report.rejected==1


@pytest.mark.integration
def test_postgres_cardinality_policy_enforces_many_and_single_overlap():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    db=PostgresMemory(dsn)
    tenant="cardinality-"+uuid4().hex
    try:
        db.migrate()
        db.set_predicate_cardinality(tenant_id=tenant,predicate="HAS_WINDOW",cardinality="many")
        a=db.apply_candidate(tenant_id=tenant,subject_id="living-room",predicate="HAS_WINDOW",
            value={"target_id":"window-a"},source_id="s1")
        b=db.apply_candidate(tenant_id=tenant,subject_id="living-room",predicate="HAS_WINDOW",
            value={"target_id":"window-b"},source_id="s2")
        duplicate=db.apply_candidate(tenant_id=tenant,subject_id="living-room",predicate="HAS_WINDOW",
            value={"target_id":"window-a"},source_id="s3")
        assert a[2] is False and b[2] is False
        assert duplicate[2] is True
        active=db.recall(tenant_id=tenant,subject_id="living-room",predicate="HAS_WINDOW")
        assert {r["value"]["target_id"] for r in active}=={"window-a","window-b"}
        assert db.conn.execute(
            "SELECT count(*) FROM memory_outbox WHERE tenant_id=%s",(tenant,)).fetchone()[0]==2
        assert db.conn.execute(
            "SELECT count(*) FROM memory_claim_sources WHERE tenant_id=%s",(tenant,)).fetchone()[0]==3
        with pytest.raises(ValueError,match="existing predicate"):
            db.set_predicate_cardinality(tenant_id=tenant,predicate="HAS_WINDOW",cardinality="one")

        single_tenant="single-"+uuid4().hex
        now=db.current_time()
        db.remember(tenant_id=single_tenant,subject_id="room",predicate="temperature",
            value={"c":21},source_id="sensor-1",valid_from=now)
        with pytest.raises(ValueError,match="overlapping active claim"):
            db.remember(tenant_id=single_tenant,subject_id="room",predicate="temperature",
                value={"c":22},source_id="sensor-2",valid_from=now)
    finally:
        db.close()
