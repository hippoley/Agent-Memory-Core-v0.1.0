"""Task creation atomically establishes a stable Temporal dispatch intent."""
import os
from uuid import uuid4
import pytest


@pytest.mark.integration
def test_create_task_atomically_creates_deterministic_temporal_dispatch():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.durable_tasks import DurableTaskStore
    from agent_memory_core.temporal_dispatch import stable_workflow_id
    db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    tenant="task-dispatch-create-"+uuid4().hex
    try:
        db.migrate()
        store=DurableTaskStore(db.conn)
        task=store.create_task(tenant_id=tenant,task_type="dispatch-test",
                               input={"value":1},idempotency_key="same-request")
        replay=store.create_task(tenant_id=tenant,task_type="dispatch-test",
                                  input={"value":1},idempotency_key="same-request")
        assert replay["task_id"]==task["task_id"]
        expected=stable_workflow_id(tenant,task["task_id"])
        assert task["workflow_id"]==expected
        row=db.conn.execute(
            """SELECT workflow_id,task_queue,status,lease_epoch
               FROM memory_temporal_dispatches WHERE tenant_id=%s AND task_id=%s::uuid""",
            (tenant,task["task_id"])).fetchone()
        assert row==(expected,"agent-memory-core","pending",0)
        assert db.conn.execute(
            """SELECT count(*) FROM memory_temporal_dispatches
               WHERE tenant_id=%s""",(tenant,)).fetchone()[0]==1
    finally:
        db.close()
