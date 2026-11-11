"""Consolidation, contradiction classification and soft forgetting."""
import json
from datetime import datetime, timezone
import pytest

from agent_memory_core.processing import (
    MemoryProcessor, classify_relation, deep_merge,
    RELATION_CREATED, RELATION_DUPLICATE, RELATION_SUPERSEDES,
    RELATION_CONTRADICTION, RELATION_MERGED,
)


class FakeStore:
    """Non-atomic store double exercising the processor fallback path."""
    def __init__(self):
        self.rows=[]
        self.sources=[]
    def recall(self, *, tenant_id, subject_id, predicate, valid_at, known_at):
        return [dict(r) for r in reversed(self.rows)
                if r["tenant_id"]==tenant_id and r["subject_id"]==subject_id
                and r["predicate"]==predicate and r["superseded_at"] is None]
    def remember(self, *, tenant_id, subject_id, predicate, value, source_id,
                 valid_from, replaces_id=None, **_):
        if replaces_id:
            for row in self.rows:
                if row["id"]==replaces_id:
                    row["superseded_at"]=datetime.now(timezone.utc)
        mid=f"memory-{len(self.rows)+1}"
        self.rows.append({"id":mid,"tenant_id":tenant_id,
            "subject_id":subject_id,"predicate":predicate,"value":value,
            "superseded_at":None})
        return mid
    def link_claim_source(self, **kwargs):
        self.sources.append(kwargs)
        return True


def test_deep_merge_unions_and_reports_scalar_conflicts():
    merged=deep_merge({"a":{"x":1},"tags":["t1"]},{"a":{"y":2},"tags":["t2"],"b":3})
    assert merged=={"a":{"x":1,"y":2},"tags":["t1","t2"],"b":3}
    assert deep_merge({"level":"quiet"},{"level":"loud"}) is None
    assert deep_merge({"level":"quiet"},{"level":"quiet"})=={"level":"quiet"}


def test_classify_relation_vocabulary():
    assert classify_relation({"level":"quiet"},{"level":"quiet"})==RELATION_DUPLICATE
    # Candidate adds nothing: subsumed, treated as duplicate.
    assert classify_relation({"level":"quiet"},{"level":"quiet","hours":"night"})==RELATION_DUPLICATE
    assert classify_relation({"level":"quiet"},{"volume":"low"})==RELATION_MERGED
    assert classify_relation({"level":"quiet"},{"level":"loud"})==RELATION_CONTRADICTION


def test_processor_merges_consolidatable_candidates():
    store=FakeStore()
    processor=MemoryProcessor(store)
    report=processor.process(tenant_id="home",text="dialogue",source_id="c1",
        extractor=lambda _:[
            {"subject_id":"room-1","predicate":"environment","value":{"light":"dim"}},
            {"subject_id":"room-1","predicate":"environment","value":{"temperature":22},
             "merge":True},
        ])
    assert report.created==["memory-1","memory-2"]
    assert report.replaced==["memory-1"]
    assert [r["relation"] for r in report.relations]==[RELATION_CREATED,RELATION_MERGED]
    active=store.recall(tenant_id="home",subject_id="room-1",predicate="environment",
                        valid_at=datetime.now(timezone.utc),
                        known_at=datetime.now(timezone.utc))
    assert len(active)==1
    assert active[0]["value"]=={"light":"dim","temperature":22}


def test_processor_reports_contradictions_and_versions_them():
    store=FakeStore()
    processor=MemoryProcessor(store)
    report=processor.process(tenant_id="home",text="dialogue",source_id="c1",
        extractor=lambda _:[
            {"subject_id":"room-1","predicate":"noise","value":{"level":"quiet"}},
            {"subject_id":"room-1","predicate":"noise","value":{"level":"loud"},
             "merge":True},
        ])
    assert report.created==["memory-1","memory-2"]
    assert report.replaced==["memory-1"]
    assert report.relations[-1]["relation"]==RELATION_CONTRADICTION
    active=store.recall(tenant_id="home",subject_id="room-1",predicate="noise",
                        valid_at=datetime.now(timezone.utc),
                        known_at=datetime.now(timezone.utc))
    # Contradictions are never silently merged: candidate value wins by versioning.
    assert active[0]["value"]=={"level":"loud"}


def test_processor_duplicate_relations_do_not_create_claims():
    store=FakeStore()
    processor=MemoryProcessor(store)
    report=processor.process(tenant_id="home",text="d",source_id="c1",
        extractor=lambda _:[
            {"subject_id":"x","predicate":"p","value":{"a":1}},
            {"subject_id":"x","predicate":"p","value":{"a":1}},
        ])
    assert report.duplicates==1
    assert report.relations[-1]=={"subject_id":"x","predicate":"p",
                                  "relation":RELATION_DUPLICATE,
                                  "memory_id":"memory-1","merged":False}


def test_sqlite_soft_forget_excludes_from_recall_and_keeps_history(tmp_path):
    from agent_memory_core.store import MemoryStore
    db=MemoryStore(tmp_path/"memory.db")
    try:
        mid=db.remember(tenant_id="home",subject_id="bedroom",predicate="noise",
                        value=json.dumps({"level":"quiet"}),
                        source_id="dialogue-1",
                        valid_from="2026-11-11T00:00:00+00:00")
        assert db.recall(tenant_id="home",subject_id="bedroom")
        # Snapshot knowledge time while the claim is still believed.
        snapshot=datetime.now(timezone.utc).isoformat()
        assert db.forget(tenant_id="home",memory_id=mid) is True
        # Second forget is idempotent false; unknown id is false.
        assert db.forget(tenant_id="home",memory_id=mid) is False
        assert db.forget(tenant_id="other",memory_id=mid) is False
        assert db.recall(tenant_id="home",subject_id="bedroom")==[]
        # Bitemporal audit history is retained: recall as known at snapshot.
        still_there=db.recall(tenant_id="home",subject_id="bedroom",
                              valid_at=snapshot,known_at=snapshot)
        assert [r.id for r in still_there]==[mid]
        events=db.events(tenant_id="home")
        assert any(e["event_type"]=="memory.claim.forgotten" for e in events)
    finally:
        db.close()


def test_forget_requires_identity():
    from agent_memory_core.store import MemoryStore
    db=MemoryStore(":memory:")
    try:
        with pytest.raises(ValueError):
            db.forget(tenant_id="",memory_id="x")
    finally:
        db.close()
