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
