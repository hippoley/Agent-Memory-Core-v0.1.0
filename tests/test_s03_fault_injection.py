"""S03 fault-injection acceptance against a real PostgreSQL transaction.

The injected exception fires after prior canonical INSERT statements have
executed on the same transaction. Neither unit-test fake storage nor an
external service is used. This does not claim OS process-kill durability.
"""
import os
from datetime import datetime,timezone
from uuid import uuid4
import pytest

pytestmark=pytest.mark.integration

class FaultingConnection:
    def __init__(self,inner,needle):
        self.inner=inner
        self.needle=needle
        self.injected=False

    def transaction(self,*args,**kwargs):
        return self.inner.transaction(*args,**kwargs)

    def execute(self,sql,params=None):
        if self.needle in str(sql):
            self.injected=True
            raise RuntimeError("intentional fault after partial canonical writes")
        return self.inner.execute(sql,params)

    def close(self):
        return self.inner.close()

@pytest.mark.parametrize("fail_at",[
    "INSERT INTO memory_outbox",
    "INSERT INTO memory_projection_deliveries",
])
def test_failed_canonical_write_rolls_back_all_tables_and_can_retry(fail_at):
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    db=PostgresMemory(dsn)
    tenant="s03-fault-"+uuid4().hex
    source="dialogue-"+uuid4().hex
    try:
        db.migrate()
        actual=db.conn
        fault=FaultingConnection(actual,fail_at)
        db.conn=fault
        kwargs=dict(tenant_id=tenant,subject_id="bedroom-1",
            predicate="night_mode",value={"enabled":True},
            source_id=source,valid_from=datetime(2026,1,1,tzinfo=timezone.utc))
        try:
            with pytest.raises(RuntimeError,match="intentional fault"):
                db.remember(**kwargs)
        finally:
            db.conn=actual
        assert fault.injected, "The intended fault boundary was not exercised"
        # A rejected transaction must not retain a canonical claim, source,
        # provenance row, event, outbox item, or independent projection task.
        for table in ("memory_claims","memory_sources","memory_claim_sources",
                      "memory_events","memory_outbox"):
            count=db.conn.execute(
                f"SELECT count(*) FROM {table} WHERE tenant_id=%s",(tenant,)
            ).fetchone()[0]
            assert count==0, f"orphaned row after rollback in {table}"
        count=db.conn.execute(
            """SELECT count(*) FROM memory_projection_deliveries d
               JOIN memory_outbox o USING (event_id) WHERE o.tenant_id=%s""",
            (tenant,)).fetchone()[0]
        assert count==0
        # A retry with the same source must succeed, proving the original
        # failure did not poison the session or leave conflicting state.
        mid=db.remember(**kwargs)
        assert db.recall(tenant_id=tenant,subject_id="bedroom-1")[0]["id"]==mid
        assert db.conn.execute(
            "SELECT count(*) FROM memory_outbox WHERE tenant_id=%s",(tenant,)
        ).fetchone()[0]==1
        assert db.conn.execute(
            """SELECT count(*) FROM memory_projection_deliveries d
               JOIN memory_outbox o USING (event_id)
               WHERE o.tenant_id=%s AND d.event_id=%s::uuid""",
            (tenant,mid)).fetchone()[0]==3
    finally:
        db.close()
