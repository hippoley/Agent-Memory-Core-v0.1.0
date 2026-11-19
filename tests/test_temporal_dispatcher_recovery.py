"""Real Temporal tests for ambiguous-start recovery and identity rejection."""
from __future__ import annotations

import asyncio
import pytest

pytestmark = pytest.mark.integration


def test_dispatcher_recovers_matching_already_started_workflow():
    pytest.importorskip("temporalio")
    from temporalio.testing import WorkflowEnvironment
    from agent_memory_core.durable_task_workflow import DurableTaskWorkflow
    from agent_memory_core.temporal_dispatcher import _start_or_recover

    async def scenario():
        async with await WorkflowEnvironment.start_local() as env:
            identity={
                "tenant_id":"tenant-dispatch-recovery",
                "task_id":"00000000-0000-0000-0000-000000000031",
                "workflow_id":"amc-dispatch-recovery-match",
                "task_type":"example.memory.batch.organize",
            }
            queue="dispatch-recovery-match"
            first=await env.client.start_workflow(
                DurableTaskWorkflow.run,identity,id=identity["workflow_id"],
                task_queue=queue,memo={"agent_memory_task_identity":identity},
            )
            first_description=await first.describe()
            run_id=await _start_or_recover(
                env.client,{**identity,"task_queue":queue,
                            "lease_epoch":1,"tenant_id":identity["tenant_id"]},
            )
            assert run_id==first_description.run_id

    asyncio.run(scenario())


def test_dispatcher_rejects_already_started_workflow_with_conflicting_memo():
    pytest.importorskip("temporalio")
    from temporalio.testing import WorkflowEnvironment
    from agent_memory_core.durable_task_workflow import DurableTaskWorkflow
    from agent_memory_core.temporal_dispatcher import _start_or_recover

    async def scenario():
        async with await WorkflowEnvironment.start_local() as env:
            identity={
                "tenant_id":"tenant-dispatch-recovery",
                "task_id":"00000000-0000-0000-0000-000000000032",
                "workflow_id":"amc-dispatch-recovery-conflict",
                "task_type":"example.memory.batch.organize",
            }
            queue="dispatch-recovery-conflict"
            wrong={**identity,"task_id":"00000000-0000-0000-0000-000000000099"}
            await env.client.start_workflow(
                DurableTaskWorkflow.run,identity,id=identity["workflow_id"],
                task_queue=queue,memo={"agent_memory_task_identity":wrong},
            )
            with pytest.raises(RuntimeError,match="identity"):
                await _start_or_recover(
                    env.client,{**identity,"task_queue":queue,"lease_epoch":1},
                )

    asyncio.run(scenario())
