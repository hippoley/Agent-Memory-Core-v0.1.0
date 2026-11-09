"""Run the Agent Memory Core Temporal verification worker.

Start this process separately from the REST API:
    python -m agent_memory_core.temporal_worker
"""
import asyncio
import os


async def main() -> None:
    try:
        from temporalio.client import Client
        from temporalio.worker import Worker
    except ImportError as exc:
        raise RuntimeError("Install agent-memory-core[temporal]") from exc

    address=os.getenv("MEMORY_TEMPORAL_ADDRESS","localhost:7233")
    namespace=os.getenv("MEMORY_TEMPORAL_NAMESPACE","default")
    task_queue=os.getenv("MEMORY_TEMPORAL_TASK_QUEUE","agent-memory-core")
    client=await Client.connect(address,namespace=namespace)

    from .temporal_adapter import (
        VerifyMemoryWorkflow,
        verify_memory_record,
        write_memory_verification_receipt,
    )
    worker=Worker(
        client,
        task_queue=task_queue,
        workflows=[VerifyMemoryWorkflow],
        activities=[verify_memory_record,write_memory_verification_receipt],
    )
    await worker.run()


if __name__=="__main__":
    asyncio.run(main())
