"""S07: real PostgreSQL bitemporal correction acceptance.

This test is deliberately skipped without MEMORY_TEST_DSN. It uses the real
canonical PostgreSQL source, not an in-memory substitute.
"""
import os
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import pytest

pytestmark=pytest.mark.integration

def utc(year,month,day):
    return datetime(year,month,day,tzinfo=timezone.utc)

def test_three_version_memory_history_is_queryable_at_exact_knowledge_boundaries():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    db=PostgresMemory(dsn)
    tenant="s07-history-"+uuid4().hex
    try:
        db.migrate()
        vf=utc(2026,1,1)
        vt=utc(2027,1,1)
        a=db.remember(tenant_id=tenant,subject_id="room-1",
            predicate="preferred_temperature",value={"celsius":22},
            source_id="utterance-a",valid_from=vf,valid_to=vt)
        k_a=db.current_time()
        b=db.remember(tenant_id=tenant,subject_id="room-1",
            predicate="preferred_temperature",value={"celsius":23},
            source_id="utterance-b",valid_from=vf,valid_to=vt,replaces_id=a)
        k_b=db.current_time()
        c=db.remember(tenant_id=tenant,subject_id="room-1",
            predicate="preferred_temperature",value={"celsius":24},
            source_id="utterance-c",valid_from=vf,valid_to=vt,replaces_id=b)
        k_c=db.current_time()
        assert len({a,b,c})==3
        for known_at,expected in ((k_a,a),(k_b,b),(k_c,c)):
            rows=db.recall(tenant_id=tenant,subject_id="room-1",
                predicate="preferred_temperature",
                valid_at=utc(2026,6,1),known_at=known_at)
            assert [row["id"] for row in rows]==[expected]
        # PostgreSQL source-of-truth is explicit about [start,end) world validity.
        assert db.recall(tenant_id=tenant,subject_id="room-1",
            predicate="preferred_temperature",
            valid_at=vf-timedelta(microseconds=1),known_at=k_c)==[]
        assert [r["id"] for r in db.recall(tenant_id=tenant,
            subject_id="room-1",predicate="preferred_temperature",
            valid_at=vf,known_at=k_c)]==[c]
        assert db.recall(tenant_id=tenant,subject_id="room-1",
            predicate="preferred_temperature",valid_at=vt,known_at=k_c)==[]
        # Each recorded and superseded boundary must agree atomically.
        records=db.conn.execute(
            """SELECT id::text,recorded_at,superseded_at,replaces_id::text
               FROM memory_claims WHERE tenant_id=%s AND id=ANY(%s::uuid[])""",
            (tenant,[a,b,c])).fetchall()
        by_id={row[0]:row for row in records}
        assert by_id[a][2]==by_id[b][1]
        assert by_id[b][2]==by_id[c][1]
        assert by_id[b][3]==a and by_id[c][3]==b
        assert by_id[c][2] is None
        # No tenant may see the claim using the same subject and predicate.
        assert db.recall(tenant_id="other-"+tenant,subject_id="room-1",
            predicate="preferred_temperature",valid_at=vf,known_at=k_c)==[]
    finally:
        db.close()

def test_invalid_correction_is_rolled_back_without_outbox_or_history_mutation():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    db=PostgresMemory(dsn)
    tenant="s07-rollback-"+uuid4().hex
    try:
        db.migrate()
        old=db.remember(tenant_id=tenant,subject_id="room-1",
            predicate="night_mode",value={"enabled":True},
            source_id="dialogue-a",valid_from=utc(2026,1,1))
        before=db.conn.execute(
            "SELECT count(*) FROM memory_outbox WHERE tenant_id=%s",(tenant,)
        ).fetchone()[0]
        with pytest.raises(ValueError):
            db.remember(tenant_id=tenant,subject_id="room-1",
                predicate="night_mode",value={"enabled":False},
                source_id="dialogue-b",valid_from=utc(2026,1,1),
                replaces_id=str(uuid4()))
        with pytest.raises(ValueError):
            db.remember(tenant_id=tenant,subject_id="room-1",
                predicate="night_mode",value={"enabled":False},
                source_id="dialogue-c",valid_from=utc(2026,1,1),
                valid_to=utc(2026,1,1),replaces_id=old)
        after=db.conn.execute(
            "SELECT count(*) FROM memory_outbox WHERE tenant_id=%s",(tenant,)
        ).fetchone()[0]
        assert before==after==1
        assert [r["id"] for r in db.recall(tenant_id=tenant,
            subject_id="room-1",predicate="night_mode")]==[old]
        assert db.conn.execute(
            "SELECT superseded_at FROM memory_claims WHERE id=%s",(old,)
        ).fetchone()[0] is None
    finally:
        db.close()
