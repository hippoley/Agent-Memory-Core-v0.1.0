"""Real Temporal + PostgreSQL vertical slice for a registered durable task."""
from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def test_temporal_workflow_executes_handler_through_postgres_fenced_worker(monkeypatch):
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    pytest.importorskip("temporalio")

    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.durable_tasks import DurableTaskStore
    from agent_memory_core.durable_task_workflow import DurableTaskWorkflow
    from agent_memory_core.temporal_adapter import execute_durable_task

    monkeypatch.setenv("MEMORY_TEST_DSN",dsn)
    monkeypatch.delenv("MEMORY_DSN",raising=False)
    monkeypatch.setenv("MEMORY_WORKER_HANDLER_MODULE",
                       "agent_memory_core.example_task_handlers")
    monkeypatch.setenv("MEMORY_WORKER_LEASE_SECONDS","30")
    monkeypatch.setenv("MEMORY_TEMPORAL_REQUIRE_RLS","0")

    async def scenario():
        tenant="temporal-durable-task-"+uuid4().hex
        db=PostgresMemory(dsn)
        try:
            db.migrate()
            task=DurableTaskStore(db.conn).create_task(
                tenant_id=tenant,
                task_type="example.memory.batch.organize",
                input={"items":[{"id":"a"},{"id":"b"},{"id":"c"}]},
            )
            identity={
                "tenant_id":tenant,
                "task_id":task["task_id"],
                "workflow_id":task["workflow_id"],
                "task_type":task["task_type"],
            }
            async with await WorkflowEnvironment.start_local() as env:
                queue="durable-task-e2e-"+uuid4().hex
                # Dispatch row is created with the default queue; this test
                # exercises the Workflow/Activity vertical slice directly.
                with ThreadPoolExecutor(max_workers=2) as executor:
                    async with Worker(
                        env.client,task_queue=queue,
                        workflows=[DurableTaskWorkflow],
                        activities=[execute_durable_task],
                        activity_executor=executor,
                    ):
                        handle=await env.client.start_workflow(
                            DurableTaskWorkflow.run,identity,
                            id=identity["workflow_id"],task_queue=queue,
                        )
                        async with asyncio.timeout(60):
                            result=await handle.result()
                        assert result["identity"]==identity
                        assert result["result"]=={
                            "processed":3,"resumed_from":0
                        }

            stored=DurableTaskStore(db.conn).get_task(
                tenant_id=tenant,task_id=task["task_id"])
            assert stored["status"]=="completed"
            assert stored["checkpoint_version"]==3
            assert stored["progress_completed"]==3
            assert stored["checkpoint"]["checkpoint"]=={
                "cursor":3,"last_item_id":"c"
            }
        finally:
            db.close()

    asyncio.run(scenario())
