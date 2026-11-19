"""Read-only Temporal/PostgreSQL lifecycle divergence classification."""
import pytest

from agent_memory_core.temporal_reconciliation import assess_temporal_task


def task(status="running"):
    return {"task_id":"task-1","workflow_id":"tenant:task-1","status":status}


def test_missing_temporal_execution_is_not_automatically_restarted():
    result=assess_temporal_task(task=task(),temporal_status=None,temporal_exists=False)
    assert result.classification=="TEMPORAL_EXECUTION_MISSING"
    assert result.safe_for_automatic_repair is False


def test_temporal_completed_does_not_complete_postgres_task():
    result=assess_temporal_task(task=task(),temporal_status="COMPLETED")
    assert result.classification=="TEMPORAL_COMPLETED_POSTGRES_NONTERMINAL"
    assert result.safe_for_automatic_repair is False


@pytest.mark.parametrize("status",["CANCELED","CANCELLED","TERMINATED","TIMED_OUT"])
def test_temporal_stop_does_not_cancel_postgres_task(status):
    result=assess_temporal_task(task=task(),temporal_status=status)
    assert result.classification=="TEMPORAL_STOPPED_POSTGRES_NONTERMINAL"
    assert result.safe_for_automatic_repair is False


def test_temporal_failure_does_not_mark_business_task_failed():
    result=assess_temporal_task(task=task(),temporal_status="FAILED")
    assert result.classification=="TEMPORAL_FAILED_POSTGRES_NONTERMINAL"


def test_unresolved_effect_blocks_completed_task_assessment():
    result=assess_temporal_task(
        task=task("completed"),temporal_status="COMPLETED",
        effects=[{"effect_key":"charge-card","status":"unknown"}])
    assert result.classification=="PG_COMPLETED_WITH_UNRESOLVED_EFFECTS"
    assert result.unresolved_effect_keys==("charge-card",)


def test_missing_postgres_task_is_reported_as_orphan_temporal():
    result=assess_temporal_task(task=None,temporal_status="RUNNING",temporal_exists=True)
    assert result.classification=="ORPHAN_TEMPORAL"


def test_aligned_nonterminal_states_remain_observational():
    result=assess_temporal_task(task=task(),temporal_status="RUNNING")
    assert result.classification=="ALIGNED_NONTERMINAL"
    assert result.safe_for_automatic_repair is False


def test_terminal_records_are_not_overwritten():
    result=assess_temporal_task(task=task("failed"),temporal_status="COMPLETED")
    assert result.classification=="TERMINAL_STATE_DIVERGENCE"

# Live adapter tests use a DB cursor and Temporal client contract double. The
# production entrypoint itself uses a real psycopg connection and Temporal SDK.
import asyncio
from types import SimpleNamespace


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _ReadOnlyConn:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, query, params):
        self.calls.append((query, params))
        return _Rows(self.rows)


class _Handle:
    def __init__(self, description=None, error=None):
        self.description = description
        self.error = error

    async def describe(self):
        if self.error:
            raise self.error
        return self.description


class _TemporalClient:
    def __init__(self, handle):
        self.handle = handle

    def get_workflow_handle(self, workflow_id):
        return self.handle


def _live_row(*, task_status="running", dispatch_status="started",
              run_id="run-1", effects=None):
    return (
        "task-1", "tenant-a", "example.task", "wf-1", task_status,
        dispatch_status, "queue-a", run_id, None, 2, 1, None,
        effects or [],
    )


class _Description:
    def __init__(self, *, status, workflow_type, task_queue, memo, run_id):
        self.id = "wf-1"
        self.status = SimpleNamespace(name=status)
        self.workflow_type = SimpleNamespace(name=workflow_type)
        self.task_queue = task_queue
        self._memo = memo
        self.run_id = run_id

    async def memo(self):
        return self._memo


def _description(*, status="RUNNING", workflow_type="DurableTaskWorkflow",
                 task_queue="queue-a", memo=None, run_id="run-1"):
    return _Description(
        status=status,
        workflow_type=workflow_type,
        task_queue=task_queue,
        memo=memo or {"agent_memory_task_identity": {
            "tenant_id": "tenant-a", "task_id": "task-1",
            "workflow_id": "wf-1", "task_type": "example.task",
        }},
        run_id=run_id,
    )


def test_live_inspection_detects_temporal_completed_postgres_running():
    from agent_memory_core.temporal_reconciliation import inspect_temporal_dispatches
    conn = _ReadOnlyConn([_live_row()])
    client = _TemporalClient(_Handle(_description(status="COMPLETED")))
    result = asyncio.run(inspect_temporal_dispatches(
        conn, client, tenant_id="tenant-a"))
    assert result[0]["classification"] == "TEMPORAL_COMPLETED_POSTGRES_NONTERMINAL"
    assert result[0]["safe_for_automatic_repair"] is False
    assert conn.calls[0][1] == ("tenant-a", 100)
    assert "WHERE t.tenant_id=%s" in conn.calls[0][0]


def test_live_inspection_distinguishes_missing_workflow_from_lookup_failure():
    from agent_memory_core.temporal_reconciliation import inspect_temporal_dispatches

    class WorkflowNotFoundError(Exception):
        status = SimpleNamespace(name="NOT_FOUND")

    missing = asyncio.run(inspect_temporal_dispatches(
        _ReadOnlyConn([_live_row()]),
        _TemporalClient(_Handle(error=WorkflowNotFoundError())),
        tenant_id="tenant-a"))
    assert missing[0]["classification"] == "POSTGRES_DISPATCH_STARTED_TEMPORAL_MISSING"

    unavailable = asyncio.run(inspect_temporal_dispatches(
        _ReadOnlyConn([_live_row()]),
        _TemporalClient(_Handle(error=ConnectionError())),
        tenant_id="tenant-a"))
    assert unavailable[0]["classification"] == "TEMPORAL_LOOKUP_FAILED"
    assert unavailable[0]["safe_for_automatic_repair"] is False


def test_live_inspection_rejects_temporal_identity_conflict():
    from agent_memory_core.temporal_reconciliation import inspect_temporal_dispatches
    description = _description(task_queue="wrong-queue")
    result = asyncio.run(inspect_temporal_dispatches(
        _ReadOnlyConn([_live_row()]),
        _TemporalClient(_Handle(description)),
        tenant_id="tenant-a"))
    assert result[0]["classification"] == "TEMPORAL_IDENTITY_CONFLICT"
    assert result[0]["identity_conflict"]["task_queue_matches"] is False
    assert result[0]["safe_for_automatic_repair"] is False


def test_live_inspection_surfaces_unresolved_effects_and_run_id_divergence():
    from agent_memory_core.temporal_reconciliation import inspect_temporal_dispatches
    row = _live_row(task_status="completed", run_id="recorded-run",
                    effects=[{"effect_key": "provider-write", "status": "unknown"}])
    result = asyncio.run(inspect_temporal_dispatches(
        _ReadOnlyConn([row]),
        _TemporalClient(_Handle(_description(status="COMPLETED", run_id="actual-run"))),
        tenant_id="tenant-a"))
    assert result[0]["classification"] == "TEMPORAL_RUN_ID_DIVERGENCE"
    assert result[0]["unresolved_effect_keys"] == ["provider-write"]
    assert result[0]["safe_for_automatic_repair"] is False


def test_live_inspection_rejects_invalid_scope_and_limit():
    from agent_memory_core.temporal_reconciliation import inspect_temporal_dispatches
    conn = _ReadOnlyConn([])
    client = _TemporalClient(_Handle(_description()))
    with pytest.raises(ValueError, match="tenant_id"):
        asyncio.run(inspect_temporal_dispatches(conn, client, tenant_id=""))
    with pytest.raises(ValueError, match="limit"):
        asyncio.run(inspect_temporal_dispatches(conn, client, tenant_id="tenant-a", limit=501))
    assert conn.calls == []

@pytest.mark.integration


def test_live_inspection_classifies_existing_workflow_without_dispatch_ack():
    from agent_memory_core.temporal_reconciliation import inspect_temporal_dispatches
    result = asyncio.run(inspect_temporal_dispatches(
        _ReadOnlyConn([_live_row(dispatch_status="dispatching")]),
        _TemporalClient(_Handle(_description(status="RUNNING"))),
        tenant_id="tenant-a"))
    assert result[0]["classification"] == "TEMPORAL_EXISTS_DISPATCH_UNACKNOWLEDGED"
    assert result[0]["safe_for_automatic_repair"] is False


def test_live_inspection_classifies_temporal_execution_with_pending_dispatch():
    from agent_memory_core.temporal_reconciliation import inspect_temporal_dispatches
    result = asyncio.run(inspect_temporal_dispatches(
        _ReadOnlyConn([_live_row(dispatch_status="pending")]),
        _TemporalClient(_Handle(_description(status="RUNNING"))),
        tenant_id="tenant-a"))
    assert result[0]["classification"] == "TEMPORAL_EXISTS_DISPATCH_PENDING"
    assert result[0]["safe_for_automatic_repair"] is False


def test_live_inspection_does_not_hide_identity_conflict_with_run_id_divergence():
    from agent_memory_core.temporal_reconciliation import inspect_temporal_dispatches
    result = asyncio.run(inspect_temporal_dispatches(
        _ReadOnlyConn([_live_row(run_id="recorded-run")]),
        _TemporalClient(_Handle(_description(
            task_queue="wrong-queue", run_id="actual-run"))),
        tenant_id="tenant-a"))
    assert result[0]["classification"] == "TEMPORAL_IDENTITY_CONFLICT"
    assert result[0]["identity_conflict"]["task_queue_matches"] is False


def test_live_postgres_temporal_dispatch_reconciliation_reads_real_services():
    """Exercise the adapter against PostgreSQL 16 and the real Temporal service."""
    import os
    from uuid import uuid4
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL is required; Temporal defaults to localhost:7233")

    from temporalio.client import Client
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.temporal_dispatch import TemporalDispatchStore, stable_workflow_id
    from agent_memory_core.temporal_reconciliation import inspect_temporal_dispatches
    from agent_memory_core.durable_task_workflow import DurableTaskWorkflow

    async def scenario():
        db = PostgresMemory(os.environ["MEMORY_TEST_DSN"])
        tenant_id = "temporal-reconcile-live-" + uuid4().hex
        task_id = str(uuid4())
        workflow_id = stable_workflow_id(tenant_id, task_id)
        task_queue = "temporal-reconcile-live-" + uuid4().hex
        client = None
        handle = None
        try:
            client = await Client.connect(
                os.getenv("MEMORY_TEMPORAL_ADDRESS", "localhost:7233"),
                namespace=os.getenv("MEMORY_TEMPORAL_NAMESPACE", "default"),
            )
            db.migrate()
            with db.conn.transaction():
                db.conn.execute(
                    """INSERT INTO memory_durable_tasks
                       (task_id,tenant_id,task_type,workflow_id,input)
                       VALUES (%s::uuid,%s,%s,%s,%s::jsonb)""",
                    (task_id, tenant_id, "example.memory.batch.organize",
                     workflow_id, "{}"),
                )
            store = TemporalDispatchStore(db.conn)
            store.enqueue(
                tenant_id=tenant_id, task_id=task_id,
                workflow_id=workflow_id, task_queue=task_queue,
            )
            # Simulate the ambiguous-start boundary: claim committed, Temporal
            # accepts the Workflow, but PostgreSQL has not acknowledged its Run ID.
            claim = store.claim(
                dispatcher_id="reconcile-integration-dispatcher",
                lease_seconds=60,
                task_queue=task_queue,
            )
            assert claim and claim["task_id"] == task_id
            identity = {
                "tenant_id": tenant_id,
                "task_id": task_id,
                "workflow_id": workflow_id,
                "task_type": "example.memory.batch.organize",
            }
            handle = await client.start_workflow(
                DurableTaskWorkflow.run,
                identity,
                id=workflow_id,
                task_queue=task_queue,
                memo={"agent_memory_task_identity": identity},
            )
            started_description = await handle.describe()
            report = await inspect_temporal_dispatches(
                db.conn, client, tenant_id=tenant_id, limit=10,
            )
            assert len(report) == 1
            assert report[0]["classification"] == "TEMPORAL_EXISTS_DISPATCH_UNACKNOWLEDGED"
            assert report[0]["temporal_status"] == "RUNNING"
            assert report[0]["temporal_run_id"] == started_description.run_id
            assert report[0]["recorded_temporal_run_id"] is None
            assert report[0]["safe_for_automatic_repair"] is False
        finally:
            if handle is not None:
                try:
                    await handle.terminate("integration test cleanup")
                except Exception:
                    pass
            with db.conn.transaction():
                db.conn.execute(
                    "DELETE FROM memory_durable_tasks WHERE task_id=%s::uuid",
                    (task_id,),
                )
            db.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_evidence_key_revocation_serializes_with_live_verification():
    """A verifier holding FOR SHARE linearizes before a concurrent key revoke."""
    import os
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from uuid import uuid4

    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL is required")

    from datetime import datetime, timedelta, timezone
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from agent_memory_core.durable_evidence import EvidenceEnvelope
    from agent_memory_core.evidence_trust import (
        EvidenceTrustError, export_public_key, sign_evidence, verify_and_consume_evidence,
    )
    from agent_memory_core.postgres import PostgresMemory

    dsn = os.environ["MEMORY_TEST_DSN"]
    verifier_db = PostgresMemory(dsn)
    revoker_db = PostgresMemory(dsn)
    tenant_id = "evidence-revoke-race-" + uuid4().hex
    principal_id = uuid4()
    issuer = "https://revoke-race.invalid/" + uuid4().hex
    key_id = "key-" + uuid4().hex
    private_key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    evidence = EvidenceEnvelope.create(
        evidence_id="event-" + uuid4().hex, tenant_id=tenant_id,
        task_id="task-" + uuid4().hex, source="temporal_history",
        observed_at=now.isoformat(),
        payload={"task_type":"example.task","event_id":"event-immutable-1",
                 "execution_status":"COMPLETED"},
        workflow_id="workflow-" + uuid4().hex,
        run_id="run-" + uuid4().hex, lease_epoch=1,
    )
    proof = sign_evidence(
        evidence, issuer=issuer, key_id=key_id, audience="revoke-race-test",
        issued_at=now.isoformat(), expires_at=(now+timedelta(minutes=2)).isoformat(),
        nonce="nonce-" + uuid4().hex, claim_type="workflow.execution_status",
        private_key=private_key,
    )
    key_locked = threading.Event()
    revoke_started = threading.Event()
    release_verifier = threading.Event()

    class PausingConnection:
        def transaction(self):
            return verifier_db.conn.transaction()

        def execute(self, query, params):
            result = verifier_db.conn.execute(query, params)
            if "FROM memory_evidence_producer_keys" in query:
                key_locked.set()
                if not release_verifier.wait(timeout=10):
                    raise TimeoutError("test did not release verifier transaction")
            return result

    try:
        verifier_db.migrate()
        with verifier_db.conn.transaction():
            verifier_db.conn.execute(
                "INSERT INTO memory_tenants(tenant_id,display_name) VALUES (%s,%s)",
                (tenant_id,"Evidence Revocation Race"),
            )
            verifier_db.conn.execute(
                """INSERT INTO memory_principals(principal_id,principal_type,display_name)
                   VALUES (%s,'service','Evidence Revocation Race')""",
                (principal_id,),
            )
            verifier_db.conn.execute(
                """INSERT INTO memory_evidence_producer_keys
                   (issuer,key_id,principal_id,public_key,audience,not_before,expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (issuer,key_id,principal_id,export_public_key(private_key),
                 "revoke-race-test",now-timedelta(minutes=1),now+timedelta(hours=1)),
            )
            verifier_db.conn.execute(
                """INSERT INTO memory_evidence_authority_grants
                   (issuer,key_id,evidence_source,claim_type,tenant_scope,
                    task_type_scope,allow_authoritative)
                   VALUES (%s,%s,'temporal_history','workflow.execution_status',
                           %s,'example.task',false)""",
                (issuer,key_id,tenant_id),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            verify_future = pool.submit(
                verify_and_consume_evidence, PausingConnection(), evidence, proof,
                expected_audience="revoke-race-test", now=now,
            )
            assert key_locked.wait(timeout=10), "verification did not acquire producer key lock"
            def revoke_key():
                revoke_started.set()
                with revoker_db.conn.transaction():
                    revoker_db.conn.execute(
                        """UPDATE memory_evidence_producer_keys SET revoked_at=clock_timestamp()
                           WHERE issuer=%s AND key_id=%s""",
                        (issuer,key_id),
                    )
            revoke_future = pool.submit(revoke_key)
            assert revoke_started.wait(timeout=10), "revocation thread did not start"
            time.sleep(0.2)
            assert not revoke_future.done(), "revocation must wait for in-flight FOR SHARE verification"
            release_verifier.set()
            verified = verify_future.result(timeout=10)
            revoke_future.result(timeout=10)
            assert verified.fact_disposition == "NEW"

        # Once revocation commits, a fresh transaction must reject the old key.
        with pytest.raises(EvidenceTrustError, match="revoked"):
            verify_and_consume_evidence(
                revoker_db.conn, evidence,
                sign_evidence(
                    evidence, issuer=issuer, key_id=key_id,
                    audience="revoke-race-test", issued_at=now.isoformat(),
                    expires_at=(now+timedelta(minutes=2)).isoformat(),
                    nonce="nonce-" + uuid4().hex,
                    claim_type="workflow.execution_status", private_key=private_key,
                ),
                expected_audience="revoke-race-test", now=now,
            )
    finally:
        release_verifier.set()
        try:
            with revoker_db.conn.transaction():
                revoker_db.conn.execute(
                    "DELETE FROM memory_evidence_nonces WHERE issuer=%s AND key_id=%s",
                    (issuer,key_id),
                )
                revoker_db.conn.execute(
                    "DELETE FROM memory_evidence_facts WHERE tenant_id=%s",
                    (tenant_id,),
                )
                revoker_db.conn.execute(
                    "DELETE FROM memory_evidence_authority_grants WHERE issuer=%s AND key_id=%s",
                    (issuer,key_id),
                )
                revoker_db.conn.execute(
                    "DELETE FROM memory_evidence_producer_keys WHERE issuer=%s AND key_id=%s",
                    (issuer,key_id),
                )
                revoker_db.conn.execute(
                    "DELETE FROM memory_principals WHERE principal_id=%s",
                    (principal_id,),
                )
                revoker_db.conn.execute(
                    "DELETE FROM memory_tenants WHERE tenant_id=%s",
                    (tenant_id,),
                )
        finally:
            verifier_db.close()
            revoker_db.close()


@pytest.mark.integration
def test_postgres_authority_matrix_fact_deduplication_and_claim_boundaries():
    """Exercise source, claim, tenant/task scope, authoritative permission and fact identity in PostgreSQL."""
    import os
    from uuid import uuid4
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL is required")

    from datetime import datetime, timedelta, timezone
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from agent_memory_core.durable_evidence import EvidenceEnvelope
    from agent_memory_core.evidence_trust import (
        EvidenceFactConflictError, EvidenceTrustError, export_public_key,
        sign_evidence, verify_and_consume_evidence,
    )
    from agent_memory_core.postgres import PostgresMemory

    db = PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    tenant_id = "evidence-authority-" + uuid4().hex
    principal_id = uuid4()
    issuer = "https://authority-matrix.invalid/" + uuid4().hex
    key_id = "key-" + uuid4().hex
    private_key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    task_id = "task-" + uuid4().hex
    workflow_id = "workflow-" + uuid4().hex
    run_id = "run-" + uuid4().hex

    def make_evidence(evidence_id, status="COMPLETED", authoritative=False, source="temporal_history"):
        payload = {"task_type":"example.task","event_id":"history-event-1",
                   "execution_status":status}
        if authoritative:
            payload["authoritative"] = True
        if source == "provider_receipt":
            payload["effect_key"] = "provider-effect-1"
            payload["receipt_id"] = "provider-receipt-1"
        return EvidenceEnvelope.create(
            evidence_id=evidence_id, tenant_id=tenant_id, task_id=task_id,
            source=source, observed_at=now.isoformat(), payload=payload,
            workflow_id=workflow_id, run_id=run_id, lease_epoch=3,
        )

    def proof(evidence, nonce, claim_type="workflow.execution_status"):
        return sign_evidence(
            evidence, issuer=issuer, key_id=key_id, audience="authority-test",
            issued_at=now.isoformat(), expires_at=(now+timedelta(minutes=2)).isoformat(),
            nonce=nonce, claim_type=claim_type, private_key=private_key,
        )

    try:
        db.migrate()
        with db.conn.transaction():
            db.conn.execute(
                "INSERT INTO memory_tenants(tenant_id,display_name) VALUES (%s,%s)",
                (tenant_id,"Evidence Authority Matrix"),
            )
            db.conn.execute(
                """INSERT INTO memory_principals(principal_id,principal_type,display_name)
                   VALUES (%s,'service','Evidence Authority Matrix')""",
                (principal_id,),
            )
            db.conn.execute(
                """INSERT INTO memory_evidence_producer_keys
                   (issuer,key_id,principal_id,public_key,audience,not_before,expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (issuer,key_id,principal_id,export_public_key(private_key),
                 "authority-test",now-timedelta(minutes=1),now+timedelta(hours=1)),
            )
            db.conn.execute(
                """INSERT INTO memory_evidence_authority_grants
                   (issuer,key_id,evidence_source,claim_type,tenant_scope,
                    task_type_scope,allow_authoritative)
                   VALUES (%s,%s,'temporal_history','workflow.execution_status',
                           %s,'example.task',false)""",
                (issuer,key_id,tenant_id),
            )

        first = make_evidence("envelope-1")
        second = make_evidence("envelope-2")
        a = verify_and_consume_evidence(
            db.conn, first, proof(first,"nonce-"+uuid4().hex),
            expected_audience="authority-test", now=now,
        )
        b = verify_and_consume_evidence(
            db.conn, second, proof(second,"nonce-"+uuid4().hex),
            expected_audience="authority-test", now=now,
        )
        assert a.fact_disposition == "NEW"
        assert b.fact_disposition == "DUPLICATE"

        conflict = make_evidence("envelope-3", status="FAILED")
        with pytest.raises(EvidenceFactConflictError, match="conflicting semantic content"):
            verify_and_consume_evidence(
                db.conn, conflict, proof(conflict,"nonce-"+uuid4().hex),
                expected_audience="authority-test", now=now,
            )

        provider_claim = make_evidence("provider-envelope", source="provider_receipt")
        with pytest.raises(EvidenceTrustError, match="no authority grant"):
            verify_and_consume_evidence(
                db.conn, provider_claim,
                proof(provider_claim,"nonce-"+uuid4().hex,"provider.effect_committed"),
                expected_audience="authority-test", now=now,
            )

        authoritative = make_evidence("authoritative-envelope", authoritative=True)
        with pytest.raises(EvidenceTrustError, match="not authorized to assert authoritative"):
            verify_and_consume_evidence(
                db.conn, authoritative, proof(authoritative,"nonce-"+uuid4().hex),
                expected_audience="authority-test", now=now,
            )
    finally:
        with db.conn.transaction():
            db.conn.execute("DELETE FROM memory_evidence_nonces WHERE issuer=%s AND key_id=%s",(issuer,key_id))
            db.conn.execute("DELETE FROM memory_evidence_facts WHERE tenant_id=%s",(tenant_id,))
            db.conn.execute("DELETE FROM memory_evidence_authority_grants WHERE issuer=%s AND key_id=%s",(issuer,key_id))
            db.conn.execute("DELETE FROM memory_evidence_producer_keys WHERE issuer=%s AND key_id=%s",(issuer,key_id))
            db.conn.execute("DELETE FROM memory_principals WHERE principal_id=%s",(principal_id,))
            db.conn.execute("DELETE FROM memory_tenants WHERE tenant_id=%s",(tenant_id,))
        db.close()
