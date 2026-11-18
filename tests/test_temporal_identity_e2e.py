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


@pytest.mark.parametrize("revocation_kind", ["session", "api_credential"])
def test_queued_temporal_work_is_rejected_after_identity_revoke(revocation_kind):
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

            credential = identity.issue_api_credential(
                principal_id=principal,
                tenant_id=tenant,
                name="Temporal E2E queued credential",
                scopes=["memory:write"],
            )

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
                        "session_id": session["session_id"] if revocation_kind == "session" else None,
                        "credential_id": credential["credential_id"] if revocation_kind == "api_credential" else None,
                        "membership_version": membership["membership_version"],
                    },
                    id=workflow_id,
                    task_queue=queue,
                )

                # The workflow is durably queued, but no Worker has been
                # started yet. Revocation therefore happens deterministically
                # before either activity can produce a side effect.
                if revocation_kind == "session":
                    assert identity.revoke_session(
                        session_id=session["session_id"],
                        principal_id=principal,
                    )
                else:
                    assert identity.revoke_api_credential(
                        credential_id=credential["credential_id"],
                        principal_id=principal,
                    )

                # Count actual Activity invocations. This proves that a terminal
                # authorization denial is not retried and that the receipt writer
                # is never entered for revoked work.
                from temporalio import activity
                from temporalio.exceptions import ApplicationError

                attempts = {"verify": 0, "receipt": 0}

                @activity.defn(name="verify_memory_record")
                def counted_verify_memory_record(request: dict) -> dict:
                    attempts["verify"] += 1
                    return verify_memory_record(request)

                @activity.defn(name="write_memory_verification_receipt")
                def counted_write_memory_verification_receipt(verified: dict) -> dict:
                    attempts["receipt"] += 1
                    return write_memory_verification_receipt(verified)

                # These activities intentionally remain synchronous because they
                # perform blocking PostgreSQL work. Temporal requires an executor.
                with ThreadPoolExecutor(max_workers=2) as activity_executor:
                    try:
                        async with Worker(
                            env.client,
                            task_queue=queue,
                            workflows=[VerifyMemoryWorkflow],
                            activities=[
                                counted_verify_memory_record,
                                counted_write_memory_verification_receipt,
                            ],
                            activity_executor=activity_executor,
                        ):
                            # A revoked queued workflow must terminate promptly;
                            # guard against regressions to unbounded Temporal retries.
                            async with asyncio.timeout(90):
                                await handle.result()
                    except Exception as exc:
                        # Temporal wraps an Activity ApplicationError in an
                        # ActivityError and then a WorkflowFailureError. Inspect
                        # the causal chain rather than matching the outer message.
                        chain = []
                        pending = [exc]
                        seen = set()
                        while pending:
                            current = pending.pop(0)
                            if id(current) in seen:
                                continue
                            seen.add(id(current))
                            chain.append(current)
                            for candidate in (
                                getattr(current, "cause", None),
                                getattr(current, "__cause__", None),
                                getattr(current, "__context__", None),
                            ):
                                if isinstance(candidate, BaseException):
                                    pending.append(candidate)

                        denial = next(
                            (
                                item
                                for item in chain
                                if isinstance(item, ApplicationError)
                                and item.type == "AuthorizationDenied"
                            ),
                            None,
                        )
                        assert denial is not None, (
                            "Expected Temporal cause chain to contain "
                            "ApplicationError(type='AuthorizationDenied'); got: "
                            + " -> ".join(
                                f"{type(item).__name__}: {item}" for item in chain
                            )
                        )
                        assert denial.non_retryable is True
                    else:
                        pytest.fail(
                            "Revoked queued workflow unexpectedly completed successfully"
                        )

                assert attempts == {"verify": 1, "receipt": 0}, (
                    "Revoked work must execute exactly one authorization check "
                    "and must never enter the receipt-writing Activity"
                )

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
