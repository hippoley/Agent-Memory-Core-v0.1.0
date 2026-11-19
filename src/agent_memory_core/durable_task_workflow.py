"""Generic durable-task orchestration shell for PostgreSQL-backed tasks.

The workflow deliberately does not claim business completion. A task-specific
runner must signal a terminal result only after its PostgreSQL lease and effect
ledger have reached a safe terminal state.
"""
from __future__ import annotations

try:
    from temporalio import workflow
except ImportError as exc:
    raise RuntimeError("Install agent-memory-core[temporal]") from exc


@workflow.defn
class DurableTaskWorkflow:
    def __init__(self):
        self._identity = None
        self._stage = "STARTING"
        self._result = None
        self._failure = None

    @workflow.query
    def identity(self) -> dict | None:
        return self._identity

    @workflow.query
    def progress(self) -> dict:
        return {
            "stage": self._stage,
            "identity": self._identity,
            "result": self._result,
            "failure": self._failure,
        }

    @workflow.signal
    def complete(self, result: dict) -> None:
        self._result = result
        self._stage = "COMPLETED"

    @workflow.signal
    def fail(self, error: dict) -> None:
        self._failure = error
        self._stage = "FAILED"

    @workflow.run
    async def run(self, request: dict) -> dict:
        required = ("tenant_id", "task_id", "workflow_id", "task_type")
        if any(not isinstance(request.get(key), str) or not request[key] for key in required):
            raise ValueError("Durable task workflow identity is incomplete")
        actual_id = workflow.info().workflow_id
        if actual_id != request["workflow_id"]:
            raise ValueError("Temporal Workflow ID does not match the persisted task identity")
        self._identity = {key: request[key] for key in required}
        self._stage = "ACCEPTED"
        await workflow.wait_condition(lambda: self._stage in {"COMPLETED", "FAILED"})
        if self._stage == "FAILED":
            raise RuntimeError("Durable task runner reported failure")
        return {"identity": self._identity, "result": self._result}
