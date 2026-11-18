"""Exercise a real Temporal local dev service and PostgreSQL activity receipts."""
import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4
import pytest

@pytest.mark.integration
def test_temporal_postgres_and_idempotent_receipt():
    if not os.environ.get("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL required")
    pytest.importorskip("temporalio")
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.temporal_adapter import (
        VerifyMemoryWorkflow,verify_memory_record,write_memory_verification_receipt)

    async def scenario():
        tenant="temporal-"+uuid4().hex
        db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
        try:
            db.migrate()
            claim=db.remember(tenant_id=tenant,subject_id="bedroom-window",
                predicate="preferred_angle",value={"degrees":17.5},
                source_id="user-1",valid_from=datetime(2026,11,1,tzinfo=timezone.utc))
        finally:
            db.close()
        from concurrent.futures import ThreadPoolExecutor
        async with await WorkflowEnvironment.start_local() as env:
            queue="memory-"+uuid4().hex
            request={"tenant_id":tenant,"memory_id":claim}
            handle=await env.client.start_workflow(VerifyMemoryWorkflow.run,request,
                id="verify-"+claim,task_queue=queue)
            # Worker A runs the workflow to a durable pause boundary.
            with ThreadPoolExecutor(max_workers=2) as executor_a:
                async with Worker(env.client,task_queue=queue,
                activity_executor=executor_a,workflows=[VerifyMemoryWorkflow],
                activities=[verify_memory_record,write_memory_verification_receipt]):
                    await handle.signal(VerifyMemoryWorkflow.pause)
                    for _ in range(100):
                        progress=await handle.query(VerifyMemoryWorkflow.progress)
                        if progress["stage"]=="PAUSED":
                            break
                        await asyncio.sleep(0.05)
                    assert progress["stage"]=="PAUSED"
                    assert progress["paused"] is True
                    assert progress["completed_steps"]==0

            # Worker A is now gone. Signal is recorded while no Worker polls;
            # Worker B must replay the same history and continue the same ID.
            await handle.signal(VerifyMemoryWorkflow.resume)
            with ThreadPoolExecutor(max_workers=2) as executor_b:
                async with Worker(env.client,task_queue=queue,
                activity_executor=executor_b,workflows=[VerifyMemoryWorkflow],
                activities=[verify_memory_record,write_memory_verification_receipt]):
                    a=await handle.result()
                    progress=await handle.query(VerifyMemoryWorkflow.progress)
                    b=await env.client.execute_workflow(VerifyMemoryWorkflow.run,request,
                        id="reverify-"+claim,task_queue=queue)
                    assert a==b and a["active"] and a["source_id"]=="user-1"
                    assert progress["stage"]=="COMPLETED"
                    assert progress["completed_steps"]==2
                    assert progress["progress_percent"]==100
        db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
        try:
            n=db.conn.execute(
                """SELECT count(*) FROM memory_outbox WHERE tenant_id=%s
                   AND aggregate_id=%s AND event_type='memory.claim.verified'""",
                (tenant,claim)).fetchone()[0]
            assert n==1
        finally:
            db.close()
    asyncio.run(scenario())
