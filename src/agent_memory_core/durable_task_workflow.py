"""Identity-bound Temporal orchestration for PostgreSQL durable tasks."""
from __future__ import annotations
from datetime import timedelta

try:
    from temporalio import workflow
    from temporalio.common import RetryPolicy
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

    @workflow.run
    async def run(self, request: dict) -> dict:
        required = ("tenant_id", "task_id", "workflow_id", "task_type")
        if any(not isinstance(request.get(key), str) or not request[key] for key in required):
            raise ValueError("Durable task workflow identity is incomplete")
        if workflow.info().workflow_id != request["workflow_id"]:
            raise ValueError("Temporal Workflow ID does not match persisted task identity")
        self._identity = {key: request[key] for key in required}
        self._stage = "EXECUTING"
        try:
            result = await workflow.execute_activity(
                "execute_durable_task",
                self._identity,
                start_to_close_timeout=timedelta(hours=24),
                heartbeat_timeout=timedelta(seconds=45),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=2),
                    maximum_interval=timedelta(minutes=1),
                    maximum_attempts=0,
                ),
            )
            if result.get("status") != "completed":
                raise RuntimeError("Durable task activity returned a non-completed state")
            self._result = result.get("result")
            self._stage = "COMPLETED"
            return {"identity": self._identity, "result": self._result}
        except Exception as exc:
            self._failure = type(exc).__name__
            self._stage = "FAILED"
            raise
