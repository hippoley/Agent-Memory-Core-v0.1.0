"""Concurrent candidate application must not create duplicate canonical facts."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from uuid import uuid4
import pytest

@pytest.mark.integration
def test_concurrent_memory_processing_is_atomic_per_tenant_subject_predicate():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.processing import MemoryProcessor
    tenant="processor-race-"+uuid4().hex
    subject="room-"+uuid4().hex
    value={"noise_level":"quiet","source_language":"zh"}
    def apply(index):
        db=PostgresMemory(dsn)
        try:
            processor=MemoryProcessor(db)
            return processor.process(tenant_id=tenant,
                text="晚上卧室保持安静",source_id=f"dialogue:{index}",
                extractor=lambda _: [{"subject_id":subject,
                    "predicate":"night_noise","value":value,"confidence":0.99}])
        finally:
            db.close()
    with ThreadPoolExecutor(max_workers=20) as pool:
        reports=list(pool.map(apply,range(20)))
    db=PostgresMemory(dsn)
    try:
        claims=db.conn.execute(
            """SELECT id::text FROM memory_claims
               WHERE tenant_id=%s AND subject_id=%s AND predicate='night_noise'
                 AND superseded_at IS NULL""",(tenant,subject)).fetchall()
        assert len(claims)==1
        assert sum(len(r.created) for r in reports)==1
        assert sum(r.duplicates for r in reports)==19
        assert db.conn.execute(
            """SELECT count(*) FROM memory_outbox
               WHERE tenant_id=%s AND aggregate_id=%s""",(tenant,claims[0][0])).fetchone()[0]==1
        assert db.conn.execute(
            """SELECT count(*) FROM memory_claim_sources
               WHERE tenant_id=%s AND claim_id=%s::uuid""",(tenant,claims[0][0])).fetchone()[0]==20
    finally:
        db.close()


@pytest.mark.integration
def test_real_postgres_zh_correction_stale_withdrawal_and_duplicate_replay():
    """Old-v2 retraction cannot delete newer v3; duplicate replay stays single."""
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.processing import MemoryProcessor
    tenant="memory-write-lifecycle-"+uuid4().hex
    subject="主卧"
    predicate="noise_preference"
    db=PostgresMemory(dsn)
    try:
        db.migrate()
        processor=MemoryProcessor(db)
        def submit(value,source,operation="UPSERT",expected_value=None):
            candidate={"subject_id":subject,"predicate":predicate,
                       "value":value,"operation":operation,"confidence":0.99}
            if expected_value is not None:
                candidate["expected_value"]=expected_value
            return processor.process(tenant_id=tenant,text="主卧记忆改口",
                                     source_id=source,extractor=lambda _:[candidate])
        v2=submit({"level":"quiet"},"turn-v2")
        v3=submit({"level":"normal"},"turn-v3")
        duplicate=submit({"level":"normal"},"turn-v3")
        stale=submit({"reason":"old-v2-cancel"},"turn-stale",
                     operation="RETRACT",expected_value={"level":"quiet"})
        current=db.recall(tenant_id=tenant,subject_id=subject,predicate=predicate)
        assert len(v2.created)==1 and len(v3.created)==1
        assert v3.replaced==v2.created
        assert duplicate.created==[] and duplicate.duplicates==1
        assert stale.replaced==[]
        assert len(current)==1 and current[0]["value"]=={"level":"normal"}
        assert len(db.list_claims(tenant_id=tenant,subject_id=subject,
                                  predicate=predicate,include_superseded=True))==2
        valid=submit({"reason":"explicit-withdrawal"},"turn-v4",
                     operation="RETRACT",expected_value={"level":"normal"})
        assert valid.replaced==v3.created
        assert db.recall(tenant_id=tenant,subject_id=subject,predicate=predicate)==[]
        all_rows=db.list_claims(tenant_id=tenant,subject_id=subject,
                                predicate=predicate,include_superseded=True)
        assert len(all_rows)==2
    finally:
        db.close()


@pytest.mark.integration
def test_conflicting_concurrent_revisions_serialize_without_two_active_claims():
    """Concurrent conflicting candidates must retain a linear version chain.

    The order of two racing updates is intentionally unspecified; consistency,
    losslessness and one active fact must hold in either order.
    """
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("real PostgreSQL required")
    from threading import Barrier
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.processing import MemoryProcessor
    tenant="memory-conflict-race-"+uuid4().hex
    subject="主卧"
    predicate="temperature_preference"

    def write(value,source,barrier=None):
        db=PostgresMemory(dsn)
        try:
            if barrier is not None:
                barrier.wait(timeout=10)
            return MemoryProcessor(db).process(
                tenant_id=tenant, text="主卧温度偏好修订",
                source_id=source,
                extractor=lambda _: [{
                    "subject_id":subject,"predicate":predicate,
                    "value":{"celsius":value},"confidence":0.99,
                }])
        finally:
            db.close()

    baseline=write(24,"original-v1")
    barrier=Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(write, temp, f"revision-{temp}", barrier)
                 for temp in (25,26)]
        reports=[f.result(timeout=30) for f in futures]
    db=PostgresMemory(dsn)
    try:
        active=db.recall(tenant_id=tenant,subject_id=subject,
                         predicate=predicate)
        history=db.list_claims(tenant_id=tenant,subject_id=subject,
                               predicate=predicate,include_superseded=True)
        assert len(baseline.created)==1
        assert all(len(r.created)==1 and len(r.replaced)==1 for r in reports)
        assert len(active)==1
        assert active[0]["value"]["celsius"] in (25,26)
        assert len(history)==3
        assert {row["value"]["celsius"] for row in history}=={24,25,26}
        assert len({row["id"] for row in history})==3
        assert sum(row["superseded_at"] is None for row in history)==1
        assert {x for r in reports for x in r.replaced} == (
            {baseline.created[0]} | {x for r in reports for x in r.created}
            - {active[0]["id"]}
        )
    finally:
        db.close()
