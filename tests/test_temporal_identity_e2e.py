"""End-to-end authorization boundary for queued Temporal work.

The test deliberately queues a real workflow before starting its Worker, revokes
the originating browser session, then starts the Worker. This removes timing
races: the durable workflow exists before authorization is revoked, but the
side-effecting activities cannot run until after the revoke.
"""
from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def test_queued_temporal_work_is_rejected_after_session_revoke():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    pytest.importorskip("temporalio")

    import psycopg
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    from agent_memory_core.identity import IdentityStore
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.temporal_adapter import (
        VerifyMemoryWorkflow,
        verify_memory_record,
        write_memory_verification_receipt,
    )

    async def scenario() -> None:
        suffix = uuid4().hex
        tenant = "tenant-temporal-e2e-" + suffix
        identity_migration = (
            Path(__file__).resolve().parents[1]
            / "src/agent_memory_core/resources/005_identity_foundation.sql"
        )

        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(identity_migration.read_text(encoding="utf-8"))

        identity = IdentityStore(dsn)
        db = PostgresMemory(dsn)
        try:
            db.migrate()
            principal = identity.create_principal(display_name="Temporal E2E User")
            identity.create_tenant(
                tenant_id=tenant,
                display_name="Temporal E2E Tenant",
                owner_principal_id=principal,
            )
            session = identity.issue_session(principal_id=principal)
            membership = identity.authenticate_session(
                token=session["token"], tenant_id=tenant
            )
            assert membership is not None

            memory_id = db.remember(
                tenant_id=tenant,
                subject_id="e2e",
                predicate="queued_verification",
                value={"status": "queued"},
                source_id="temporal-e2e",
                valid_from=datetime.now(timezone.utc),
            )

            async with await WorkflowEnvironment.start_local() as env:
                queue = "memory-temporal-e2e-" + suffix
                workflow_id = tenant + ":queued-revocation-" + suffix
                handle = await env.client.start_workflow(
                    VerifyMemoryWorkflow.run,
                    {
                        "tenant_id": tenant,
                        "memory_id": memory_id,
                        "principal_id": principal,
                        "session_id": session["session_id"],
                        "membership_version": membership["membership_version"],
                    },
                    id=workflow_id,
                    task_queue=queue,
                )

                # The workflow is durably queued, but no Worker has been
                # started yet. Revocation therefore happens deterministically
                # before either activity can produce a side effect.
                assert identity.revoke_session(
                    session_id=session["session_id"],
                    principal_id=principal,
                )

                # These activities intentionally remain synchronous because they
                # perform blocking PostgreSQL work. Temporal requires an executor
                # for sync activities; without it the test fails before exercising
                # the authorization check.
                with ThreadPoolExecutor(max_workers=2) as activity_executor:
                    with pytest.raises(Exception, match="Queued work is no longer authorized"):
                        async with Worker(
                            env.client,
                            task_queue=queue,
                            workflows=[VerifyMemoryWorkflow],
                            activities=[verify_memory_record, write_memory_verification_receipt],
                            activity_executor=activity_executor,
                        ):
                            await handle.result()

            receipt_count = db.conn.execute(
                """SELECT count(*) FROM memory_outbox
                   WHERE tenant_id=%s AND aggregate_id=%s::uuid
                     AND event_type='memory.claim.verified'""",
                (tenant, memory_id),
            ).fetchone()[0]
            assert receipt_count == 0
        finally:
            db.close()
            identity.close()

    asyncio.run(scenario())
