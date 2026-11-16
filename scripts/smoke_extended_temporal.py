"""Prove the opt-in Extended profile executes a REAL Temporal workflow.

Requires an isolated running extended Compose stack; never use production DB.
Run by the opt-in GitHub integration job, not by normal unit test suite.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

from agent_memory_core.postgres import PostgresMemory
from agent_memory_core.temporal_adapter import VerifyMemoryWorkflow

DSN = os.getenv(
    "MEMORY_TEST_DSN",
    "postgresql://memory:local-dev-only@127.0.0.1:55432/memory",
)


async def main():
    from temporalio.client import Client

    tenant = "extended-smoke-" + uuid4().hex
    db = PostgresMemory(DSN)
    try:
        db.migrate()
        memory_id = db.remember(
            tenant_id=tenant,
            subject_id="asset-pump-17",
            predicate="inspection_period",
            value={"days": 45},
            source_id="maintenance-bulletin-v2",
            valid_from=datetime.now(timezone.utc),
        )
    finally:
        db.close()

    client = None
    for attempt in range(40):
        try:
            client = await Client.connect("127.0.0.1:7233")
            break
        except Exception:
            if attempt == 39:
                raise
            await asyncio.sleep(2)

    request = {"tenant_id": tenant, "memory_id": memory_id}
    for prefix in ("first", "replay"):
        receipt = await asyncio.wait_for(
            client.execute_workflow(
                VerifyMemoryWorkflow.run,
                request,
                id=f"extended-smoke-{prefix}-{memory_id}",
                task_queue="agent-memory-core",
            ),
            timeout=120,
        )
        assert receipt["tenant_id"] == tenant
        assert receipt["memory_id"] == memory_id
        assert receipt["active"] is True
        assert receipt["source_id"] == "maintenance-bulletin-v2"

    db = PostgresMemory(DSN)
    try:
        total = db.conn.execute(
            """SELECT count(*) FROM memory_outbox
               WHERE tenant_id=%s AND aggregate_id=%s
                 AND event_type='memory.claim.verified'""",
            (tenant, memory_id),
        ).fetchone()[0]
        assert total == 1, f"Expected idempotent Temporal verification receipt, got {total}"
    finally:
        db.close()
    print("Extended profile: real Temporal workflow + PG receipt PASSED")


if __name__ == "__main__":
    asyncio.run(main())
