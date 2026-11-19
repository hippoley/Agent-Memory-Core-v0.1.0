"""Real PostgreSQL acceptance for lease ownership and fencing."""
import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def test_claim_is_exclusive_epoch_increments_and_old_worker_is_fenced():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.durable_tasks import DurableTaskStore
    from agent_memory_core.worker_leases import DurableTaskLeaseStore, LeaseConflict

    db=PostgresMemory(dsn)
    tenant="lease-"+uuid4().hex
    try:
        db.migrate()
        tasks=DurableTaskStore(db.conn)
        task=tasks.create_task(tenant_id=tenant,task_type="memory.batch.organize",
                               input={"count":3},idempotency_key="create-"+uuid4().hex)
        leases=DurableTaskLeaseStore(db.conn)
        a=leases.claim(tenant_id=tenant,worker_id="worker-a",lease_seconds=30)
        assert a and a["task_id"]==task["task_id"] and a["lease_epoch"]==1
        assert leases.claim(tenant_id=tenant,worker_id="worker-b",lease_seconds=30) is None
        first=leases.commit_checkpoint(
            tenant_id=tenant,task_id=task["task_id"],worker_id="worker-a",
            lease_epoch=1,expected_version=0,checkpoint={"cursor":1},
            idempotency_key="cp-1",progress_completed=1,progress_total=3)
        assert first["version"]==1

        # Simulate expiry with PostgreSQL time, then let B atomically take over.
        db.conn.execute(
            "UPDATE memory_durable_tasks SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE task_id=%s::uuid",
            (task["task_id"],))
        b=leases.claim(tenant_id=tenant,worker_id="worker-b",lease_seconds=30)
        assert b and b["lease_epoch"]==2
        assert b["checkpoint"]["cursor"]==1

        with pytest.raises(LeaseConflict,match="fenced"):
            leases.commit_checkpoint(
                tenant_id=tenant,task_id=task["task_id"],worker_id="worker-a",
                lease_epoch=1,expected_version=1,checkpoint={"cursor":2},
                idempotency_key="cp-old")
        second=leases.commit_checkpoint(
            tenant_id=tenant,task_id=task["task_id"],worker_id="worker-b",
            lease_epoch=2,expected_version=1,checkpoint={"cursor":2},
            idempotency_key="cp-2",progress_completed=2,progress_total=3)
        assert second["version"]==2
        with pytest.raises(LeaseConflict):
            leases.finish(tenant_id=tenant,task_id=task["task_id"],
                          worker_id="worker-a",lease_epoch=1,status="completed")
        leases.finish(tenant_id=tenant,task_id=task["task_id"],
                      worker_id="worker-b",lease_epoch=2,status="completed",
                      result={"processed":2})
        assert tasks.get_task(tenant_id=tenant,task_id=task["task_id"])["status"]=="completed"
    finally:
        db.close()


def test_worker_claim_is_tenant_scoped():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.durable_tasks import DurableTaskStore
    from agent_memory_core.worker_leases import DurableTaskLeaseStore

    db=PostgresMemory(dsn)
    tenant_a="tenant-a-"+uuid4().hex
    tenant_b="tenant-b-"+uuid4().hex
    try:
        db.migrate()
        DurableTaskStore(db.conn).create_task(
            tenant_id=tenant_a,task_type="memory.batch.organize",input={},
            idempotency_key="a-"+uuid4().hex)
        claim=DurableTaskLeaseStore(db.conn).claim(
            tenant_id=tenant_b,worker_id="worker-b")
        assert claim is None
    finally:
        db.close()
