"""Real Temporal tests for ambiguous-start recovery and identity rejection."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import pytest

pytestmark = pytest.mark.integration

def _write_da05_receipt(case, *, workflow_id, before, after, expected):
    """Persist proven rejection, without leaking identity memos or credentials."""
    path = os.getenv("MEMORY_DA_EVIDENCE_DIR")
    if not path:
        return
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": "amc.dispatcher-recovery.v1",
        "scenario": "DA-05-" + case,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "workflow_id": workflow_id,
        "temporal": {
            "before_run_id": before.run_id,
            "after_run_id": after.run_id,
            "before_task_queue": before.task_queue,
            "after_task_queue": after.task_queue,
            "before_workflow_type": getattr(before.workflow_type, "name", before.workflow_type),
            "after_workflow_type": getattr(after.workflow_type, "name", after.workflow_type),
        },
        "assertions": {
            "identity_rejected": True,
            "existing_execution_unchanged": True,
            "conflict_dimension": expected,
        },
        "result": "PASS",
    }
    (root / ("DA-05-" + case + ".json")).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


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
            before=await env.client.get_workflow_handle(identity["workflow_id"]).describe()
            with pytest.raises(RuntimeError,match="identity"):
                await _start_or_recover(
                    env.client,{**identity,"task_queue":queue,"lease_epoch":1},
                )
            after=await env.client.get_workflow_handle(identity["workflow_id"]).describe()
            assert before.run_id == after.run_id
            assert before.task_queue == after.task_queue == queue
            assert before.workflow_type == after.workflow_type
            _write_da05_receipt("memo",workflow_id=identity["workflow_id"],
                                before=before,after=after,expected="memo")

    asyncio.run(scenario())

def test_dispatcher_rejects_existing_workflow_on_wrong_task_queue():
    pytest.importorskip("temporalio")
    from temporalio.testing import WorkflowEnvironment
    from agent_memory_core.durable_task_workflow import DurableTaskWorkflow
    from agent_memory_core.temporal_dispatcher import _start_or_recover

    async def scenario():
        async with await WorkflowEnvironment.start_local() as env:
            identity={
                "tenant_id":"tenant-dispatch-recovery-queue",
                "task_id":"00000000-0000-0000-0000-000000000041",
                "workflow_id":"amc-dispatch-recovery-wrong-queue",
                "task_type":"example.memory.batch.organize",
            }
            await env.client.start_workflow(
                DurableTaskWorkflow.run,identity,id=identity["workflow_id"],
                task_queue="actual-queue",memo={"agent_memory_task_identity":identity},
            )
            before=await env.client.get_workflow_handle(identity["workflow_id"]).describe()
            with pytest.raises(RuntimeError,match="identity"):
                await _start_or_recover(
                    env.client,{**identity,"task_queue":"unexpected-queue","lease_epoch":2},
                )
            after=await env.client.get_workflow_handle(identity["workflow_id"]).describe()
            assert before.run_id == after.run_id
            assert before.task_queue == after.task_queue == "actual-queue"
            assert before.workflow_type == after.workflow_type
            _write_da05_receipt("task-queue",workflow_id=identity["workflow_id"],
                                before=before,after=after,expected="task_queue")

    asyncio.run(scenario())

def test_dispatcher_rejects_existing_workflow_with_wrong_workflow_type():
    pytest.importorskip("temporalio")
    from temporalio.testing import WorkflowEnvironment
    from agent_memory_core.temporal_dispatcher import _start_or_recover

    async def scenario():
        async with await WorkflowEnvironment.start_local() as env:
            identity = {
                "tenant_id": "tenant-dispatch-recovery-type",
                "task_id": "00000000-0000-0000-0000-000000000051",
                "workflow_id": "amc-dispatch-recovery-wrong-type",
                "task_type": "example.memory.batch.organize",
            }
            queue = "dispatch-recovery-type"
            # Start a valid Temporal execution under the same deterministic
            # Workflow ID, but deliberately use a different registered type.
            await env.client.start_workflow(
                "UnexpectedWorkflowType",
                identity,
                id=identity["workflow_id"],
                task_queue=queue,
                memo={"agent_memory_task_identity": identity},
            )
            description = await env.client.get_workflow_handle(
                identity["workflow_id"]
            ).describe()
            workflow_type = getattr(description, "workflow_type", None)
            workflow_type_name = getattr(workflow_type, "name", workflow_type)
            assert workflow_type_name == "UnexpectedWorkflowType"

            with pytest.raises(RuntimeError, match="identity"):
                await _start_or_recover(
                    env.client,
                    {**identity, "task_queue": queue, "lease_epoch": 3},
                )

            # The conflicting execution remains untouched; no false ACK is
            # possible because _start_or_recover raises before returning a run.
            after = await env.client.get_workflow_handle(
                identity["workflow_id"]
            ).describe()
            after_type = getattr(after.workflow_type, "name", after.workflow_type)
            assert after_type == "UnexpectedWorkflowType"
            assert after.run_id == description.run_id
            assert after.task_queue == description.task_queue
            _write_da05_receipt("workflow-type",workflow_id=identity["workflow_id"],
                                before=description,after=after,expected="workflow_type")

    asyncio.run(scenario())
