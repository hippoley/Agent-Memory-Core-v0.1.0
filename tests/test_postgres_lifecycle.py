"""Merge, contradiction classification and forgetting on the real PostgreSQL stack."""
import os
from uuid import uuid4
import pytest

@pytest.mark.integration
def test_postgres_apply_candidate_classifies_merges_and_forgets():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real database required")
    from agent_memory_core.postgres import PostgresMemory
    db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    tenant="lifecycle-"+uuid4().hex
    try:
        db.migrate()
        subject="room-"+uuid4().hex[:8]
        mid,_,dup,relation=db.apply_candidate(
            tenant_id=tenant,subject_id=subject,predicate="environment",
            value={"light":"dim"},source_id="conv-1",confidence=0.9)
        assert (mid,dup,relation)==(mid,False,"CREATED")
        # Mergeable complement consolidates into one active claim.
        mid2,replaces,dup2,relation2=db.apply_candidate(
            tenant_id=tenant,subject_id=subject,predicate="environment",
            value={"temperature":22},source_id="conv-2",confidence=0.9,
            merge=True)
        assert dup2 is False and relation2=="MERGED" and replaces==mid
        active=db.recall(tenant_id=tenant,subject_id=subject,
                         predicate="environment")
        assert len(active)==1 and active[0]["value"]=={"light":"dim","temperature":22}
        # Scalar conflict classifies as CONTRADICTION and versions forward.
        mid3,_,dup3,relation3=db.apply_candidate(
            tenant_id=tenant,subject_id=subject,predicate="environment",
            value={"light":"bright"},source_id="conv-3",confidence=0.9,
            merge=True)
        assert dup3 is False and relation3=="CONTRADICTION" and _==mid2
        active=db.recall(tenant_id=tenant,subject_id=subject,
                         predicate="environment")
        assert len(active)==1 and active[0]["value"]=={"light":"bright","temperature":22}
        # Soft forget hides from active reads; audit history remains queryable.
        assert db.forget(tenant_id=tenant,memory_id=mid3) is True
        assert db.forget(tenant_id=tenant,memory_id=mid3) is False
        assert db.recall(tenant_id=tenant,subject_id=subject,
                         predicate="environment")==[]
        forgotten_events=[e for e in db.events_after(tenant_id=tenant,limit=50)
                          if e.get("event_type")=="memory.claim.forgotten"]
        assert forgotten_events, "forget must emit a durable event"
        # Bulk forget by subject filter.
        db.remember(tenant_id=tenant,subject_id=subject,predicate="note",
                    value={"text":"x"},source_id="s",
                    valid_from=__import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc))
        forgotten=db.forget_many(tenant_id=tenant,subject_id=subject,
                                 predicate="note")
        assert len(forgotten)==1
    finally:
        db.close()
