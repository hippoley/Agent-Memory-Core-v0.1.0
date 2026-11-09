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
            with ThreadPoolExecutor(max_workers=2) as executor:
                async with Worker(env.client,task_queue=queue,
                activity_executor=executor,workflows=[VerifyMemoryWorkflow],
                activities=[verify_memory_record,write_memory_verification_receipt]):
                    request={"tenant_id":tenant,"memory_id":claim}
                    a=await env.client.execute_workflow(VerifyMemoryWorkflow.run,request,
                        id="verify-"+claim,task_queue=queue)
                    b=await env.client.execute_workflow(VerifyMemoryWorkflow.run,request,
                        id="reverify-"+claim,task_queue=queue)
                    assert a==b and a["active"] and a["source_id"]=="user-1"
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
