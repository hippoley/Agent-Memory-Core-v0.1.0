"""Run the Agent Memory Core Temporal verification worker.

Start this process separately from the REST API:
    python -m agent_memory_core.temporal_worker
"""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor


async def main() -> None:
    try:
        from temporalio.client import Client
        from temporalio.worker import Worker
    except ImportError as exc:
        raise RuntimeError("Install agent-memory-core[temporal]") from exc

    address=os.getenv("MEMORY_TEMPORAL_ADDRESS","localhost:7233")
    namespace=os.getenv("MEMORY_TEMPORAL_NAMESPACE","default")
    task_queue=os.getenv("MEMORY_TEMPORAL_TASK_QUEUE","agent-memory-core")
    # The Compose dev server starts concurrently with this process.
    # Wait for its API to accept connections before constructing the Worker.
    for attempt in range(30):
        try:
            client=await Client.connect(address,namespace=namespace)
            break
        except Exception:
            if attempt==29:
                raise
            await asyncio.sleep(2)

    from .temporal_adapter import (
        VerifyMemoryWorkflow,
        verify_memory_record,
        write_memory_verification_receipt,
    )
    # Both Activities are synchronous psycopg functions. Temporal Python
    # requires an executor for synchronous Activities, otherwise Worker
    # construction fails even when connection and unit tests succeed.
    with ThreadPoolExecutor(max_workers=4) as executor:
        worker=Worker(
            client,
            task_queue=task_queue,
            activity_executor=executor,
            workflows=[VerifyMemoryWorkflow],
            activities=[verify_memory_record,write_memory_verification_receipt],
        )
        await worker.run()


if __name__=="__main__":
    asyncio.run(main())
