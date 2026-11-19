"""Independent PostgreSQL -> Temporal dispatcher process.

Claims are fenced in PostgreSQL. Temporal calls happen outside DB transactions.
An ambiguous start is resolved by the deterministic Workflow ID and immutable
memo identity before PostgreSQL is acknowledged.
"""
from __future__ import annotations

import asyncio
import os
import socket
import uuid


def _identity(claim: dict) -> dict:
    return {
        "tenant_id": claim["tenant_id"],
        "task_id": claim["task_id"],
        "workflow_id": claim["workflow_id"],
        "task_type": claim["task_type"],
    }


async def _start_or_recover(client, claim: dict) -> str:
    from temporalio.common import WorkflowIDReusePolicy
    from temporalio.exceptions import WorkflowAlreadyStartedError
    from .durable_task_workflow import DurableTaskWorkflow

    identity = _identity(claim)
    handle = client.get_workflow_handle(identity["workflow_id"])
    try:
        handle = await client.start_workflow(
            DurableTaskWorkflow.run,
            identity,
            id=identity["workflow_id"],
            task_queue=claim["task_queue"],
            memo={"agent_memory_task_identity": identity},
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        # Do not treat AlreadyStarted as success by itself. The deterministic
        # ID, workflow type, queue and persisted memo must all match.
        description = await handle.describe()
        workflow_type = getattr(description, "workflow_type", None)
        workflow_type_name = getattr(workflow_type, "name", workflow_type)
        memo_getter = getattr(description, "memo", None)
        memo = await memo_getter() if callable(memo_getter) else (memo_getter or {})
        if (
            getattr(description, "id", None) != identity["workflow_id"]
            or workflow_type_name != "DurableTaskWorkflow"
            or getattr(description, "task_queue", None) != claim["task_queue"]
            or memo.get("agent_memory_task_identity") != identity
        ):
            raise RuntimeError("Existing Temporal execution identity does not match PostgreSQL task")
    description = await handle.describe()
    if getattr(description, "id", None) != identity["workflow_id"]:
        raise RuntimeError("Temporal returned a different Workflow ID")
    run_id = getattr(description, "run_id", None)
    if not run_id:
        raise RuntimeError("Temporal execution has no run ID")
    return run_id


async def run_dispatcher(*, once: bool = False) -> None:
    try:
        from temporalio.client import Client
    except ImportError as exc:
        raise RuntimeError("Install agent-memory-core[temporal]") from exc

    from .postgres import PostgresMemory
    from .temporal_adapter import _database_dsn
    from .temporal_dispatch import TemporalDispatchStore

    address = os.getenv("MEMORY_TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.getenv("MEMORY_TEMPORAL_NAMESPACE", "default")
    configured_queue = os.getenv("MEMORY_TEMPORAL_TASK_QUEUE", "agent-memory-core")
    dispatcher_id = os.getenv("MEMORY_TEMPORAL_DISPATCHER_ID") or (
        socket.gethostname() + ":" + str(uuid.uuid4())
    )
    lease_seconds = int(os.getenv("MEMORY_TEMPORAL_DISPATCH_LEASE_SECONDS", "60"))
    poll_seconds = float(os.getenv("MEMORY_TEMPORAL_DISPATCH_POLL_SECONDS", "2"))
    client = await Client.connect(address, namespace=namespace)

    while True:
        db = PostgresMemory(_database_dsn())
        try:
            claim = TemporalDispatchStore(db.conn).claim(
                dispatcher_id=dispatcher_id,
                lease_seconds=lease_seconds,
                task_queue=configured_queue,
            )
            if claim is not None:
                row = db.conn.execute(
                    """SELECT task_type FROM memory_durable_tasks
                       WHERE tenant_id=%s AND task_id=%s::uuid AND workflow_id=%s""",
                    (claim["tenant_id"], claim["task_id"], claim["workflow_id"]),
                ).fetchone()
                if row is None:
                    raise RuntimeError("Claimed dispatch has no matching PostgreSQL task identity")
                claim["task_type"] = row[0]
        finally:
            db.close()

        if claim is None:
            if once:
                return
            await asyncio.sleep(poll_seconds)
            continue

        try:
            run_id = await _start_or_recover(client, claim)
        except Exception as exc:
            db = PostgresMemory(_database_dsn())
            try:
                TemporalDispatchStore(db.conn).release_for_retry(
                    task_id=claim["task_id"],
                    dispatcher_id=dispatcher_id,
                    lease_epoch=claim["lease_epoch"],
                    error=f"{type(exc).__name__}: {exc}",
                )
            finally:
                db.close()
            if once:
                raise
        else:
            db = PostgresMemory(_database_dsn())
            try:
                acknowledged = TemporalDispatchStore(db.conn).mark_started(
                    task_id=claim["task_id"],
                    dispatcher_id=dispatcher_id,
                    lease_epoch=claim["lease_epoch"],
                    temporal_run_id=run_id,
                )
                if not acknowledged:
                    # A newer dispatcher owns the row. The stable Workflow ID
                    # makes its recovery idempotent; never overwrite its epoch.
                    pass
            finally:
                db.close()
        if once:
            return


def main() -> None:
    asyncio.run(run_dispatcher(once=os.getenv("MEMORY_TEMPORAL_DISPATCH_ONCE", "0") == "1"))


if __name__ == "__main__":
    main()
