"""Transactional durable task/checkpoint acceptance against real PostgreSQL."""
import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def test_checkpoint_version_idempotency_effect_intent_and_outbox_are_atomic():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.durable_tasks import DurableTaskStore,TaskConflict

    db=PostgresMemory(dsn)
    tenant="durable-task-"+uuid4().hex
    try:
        db.migrate()
        tasks=DurableTaskStore(db.conn)
        task=tasks.create_task(
            tenant_id=tenant,task_type="memory.batch.organize",
            input={"source":"acceptance","limit":100},
            idempotency_key="create-"+uuid4().hex,
        )
        task_id=task["task_id"]
        replay=tasks.create_task(
            tenant_id=tenant,task_type="memory.batch.organize",
            input={"source":"acceptance","limit":100},
            idempotency_key=next(iter(db.conn.execute(
                "SELECT idempotency_key FROM memory_durable_tasks WHERE task_id=%s::uuid",
                (task_id,)).fetchone())),
        )
        assert replay["task_id"]==task_id

        first=tasks.commit_checkpoint(
            tenant_id=tenant,task_id=task_id,expected_version=0,
            checkpoint={"last_completed":25,"next_item":26},
            idempotency_key="checkpoint-1",
            progress_completed=25,progress_total=100,
            effect_key="write-memory-claim-26",
            effect_request={"operation":"remember","item":26},
        )
        assert first=={"task_id":task_id,"version":1,"replayed":False}
        replayed=tasks.commit_checkpoint(
            tenant_id=tenant,task_id=task_id,expected_version=0,
            checkpoint={"last_completed":25,"next_item":26},
            idempotency_key="checkpoint-1",
            progress_completed=25,progress_total=100,
            effect_key="write-memory-claim-26",
            effect_request={"operation":"remember","item":26},
        )
        assert replayed["version"]==1 and replayed["replayed"] is True

        with pytest.raises(TaskConflict,match="version conflict"):
            tasks.commit_checkpoint(
                tenant_id=tenant,task_id=task_id,expected_version=0,
                checkpoint={"last_completed":26},idempotency_key="checkpoint-2",
                progress_completed=26,progress_total=100,
            )
        with pytest.raises(TaskConflict,match="different content"):
            tasks.commit_checkpoint(
                tenant_id=tenant,task_id=task_id,expected_version=0,
                checkpoint={"last_completed":999},idempotency_key="checkpoint-1",
            )

        current=tasks.commit_checkpoint(
            tenant_id=tenant,task_id=task_id,expected_version=1,
            checkpoint={"last_completed":50,"next_item":51},
            idempotency_key="checkpoint-2",
            progress_completed=50,progress_total=100,
        )
        assert current["version"]==2
        loaded=tasks.get_task(tenant_id=tenant,task_id=task_id)
        assert loaded["checkpoint_version"]==2
        assert loaded["checkpoint"]["checkpoint"]["last_completed"]==50
        assert loaded["progress_completed"]==50

        effects=db.conn.execute(
            "SELECT count(*) FROM memory_task_effects WHERE task_id=%s::uuid",
            (task_id,)).fetchone()[0]
        events=db.conn.execute(
            """SELECT event_type,count(*) FROM memory_outbox
               WHERE tenant_id=%s AND aggregate_id=%s GROUP BY event_type""",
            (tenant,task_id)).fetchall()
        assert effects==1
        assert {kind:count for kind,count in events}=={
            "memory.task.created":1,
            "memory.task.checkpoint.committed":2,
            "memory.task.effect.requested":1,
        }
        assert tasks.set_status(tenant_id=tenant,task_id=task_id,status="completed",
                                result={"organized":50})
        with pytest.raises(TaskConflict,match="terminal task"):
            tasks.commit_checkpoint(
                tenant_id=tenant,task_id=task_id,expected_version=2,
                checkpoint={"last_completed":51},idempotency_key="checkpoint-3",
            )
    finally:
        db.close()
