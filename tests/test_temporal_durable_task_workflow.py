"""Local Temporal integration tests for the identity-bound durable workflow."""
from __future__ import annotations

import asyncio
import pytest

pytestmark = pytest.mark.integration


def test_durable_task_workflow_exposes_identity_and_waits_for_terminal_signal():
    pytest.importorskip("temporalio")
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    from agent_memory_core.durable_task_workflow import DurableTaskWorkflow

    async def scenario():
        async with await WorkflowEnvironment.start_local() as env:
            queue = "durable-task-workflow-test"
            identity = {
                "tenant_id": "tenant-test",
                "task_id": "00000000-0000-0000-0000-000000000001",
                "workflow_id": "amc-test-durable-task-identity",
                "task_type": "test",
            }
            async with Worker(
                env.client, task_queue=queue, workflows=[DurableTaskWorkflow]
            ):
                handle = await env.client.start_workflow(
                    DurableTaskWorkflow.run, identity,
                    id=identity["workflow_id"], task_queue=queue,
                    memo={"agent_memory_task_identity": identity},
                )
                async with asyncio.timeout(20):
                    observed = await handle.query(DurableTaskWorkflow.identity)
                    assert observed == identity
                    progress = await handle.query(DurableTaskWorkflow.progress)
                    assert progress["stage"] == "ACCEPTED"
                    await handle.signal(DurableTaskWorkflow.complete, {"ok": True})
                    result = await handle.result()
                    assert result == {"identity": identity, "result": {"ok": True}}

    asyncio.run(scenario())


def test_durable_task_workflow_rejects_mismatched_temporal_workflow_id():
    pytest.importorskip("temporalio")
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    from agent_memory_core.durable_task_workflow import DurableTaskWorkflow

    async def scenario():
        async with await WorkflowEnvironment.start_local() as env:
            queue = "durable-task-workflow-mismatch"
            identity = {
                "tenant_id": "tenant-test",
                "task_id": "00000000-0000-0000-0000-000000000002",
                "workflow_id": "expected-workflow-id",
                "task_type": "test",
            }
            async with Worker(
                env.client, task_queue=queue, workflows=[DurableTaskWorkflow]
            ):
                handle = await env.client.start_workflow(
                    DurableTaskWorkflow.run, identity,
                    id="different-workflow-id", task_queue=queue,
                )
                with pytest.raises(Exception):
                    async with asyncio.timeout(20):
                        await handle.result()

    asyncio.run(scenario())
