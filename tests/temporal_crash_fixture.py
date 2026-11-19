"""Subprocess Worker fixture for SIGKILL recovery integration tests."""
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Worker


@activity.defn(name="crash_recovery_activity")
async def crash_recovery_activity(request: dict) -> dict:
    attempt = activity.info().attempt
    marker = Path(request["marker"])
    if attempt == 1:
        marker.write_text("worker-a-started", encoding="utf-8")
        while True:
            activity.heartbeat({"attempt": attempt})
            await asyncio.sleep(0.1)
    marker.write_text("worker-b-retried", encoding="utf-8")
    return {"attempt": attempt, "recovered": True}


@workflow.defn(name="CrashRecoveryWorkflow")
class CrashRecoveryWorkflow:
    @workflow.run
    async def run(self, request: dict) -> dict:
        return await workflow.execute_activity(
            crash_recovery_activity,
            request,
            start_to_close_timeout=timedelta(seconds=30),
            heartbeat_timeout=timedelta(seconds=2),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(milliseconds=250),
                maximum_interval=timedelta(seconds=1),
                maximum_attempts=3,
            ),
        )


async def main() -> None:
    address, task_queue = sys.argv[1], sys.argv[2]
    client = await Client.connect(address)
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[CrashRecoveryWorkflow],
        activities=[crash_recovery_activity],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
