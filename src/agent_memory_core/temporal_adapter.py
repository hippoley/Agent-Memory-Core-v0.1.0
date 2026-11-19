"""Optional Temporal adapter; durable workflow, PostgreSQL source of truth."""
from __future__ import annotations
from datetime import timedelta
from uuid import uuid5, NAMESPACE_URL
import os


def _database_dsn() -> str:
    dsn = os.getenv("MEMORY_DSN") or os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        raise RuntimeError("MEMORY_DSN is required by Temporal Activities")
    return dsn

def _assert_temporal_tenant(db, tenant_id: str) -> None:
    """Bind every Temporal activity to the PostgreSQL login's tenant identity.

    Local development may run before operator-installed RLS exists. Once the
    RLS registry is installed, an unbound/admin login is always rejected.
    Production must set MEMORY_TEMPORAL_REQUIRE_RLS=1 so a missing RLS
    installation also fails closed.
    """
    strict = os.getenv("MEMORY_TEMPORAL_REQUIRE_RLS", "0") == "1"
    # In local/dev databases the operator-installed RLS registry may be absent.
    # Probe its function without invoking a missing function (which otherwise
    # emits PostgreSQL ERROR logs on every Temporal retry).
    installed = db.conn.execute(
        "SELECT to_regprocedure('public.memory_authenticated_tenant()') IS NOT NULL"
    ).fetchone()[0]
    if not installed:
        if strict:
            raise RuntimeError("Database RLS principal mapping is not installed")
        return
    try:
        db.assert_database_tenant(tenant_id)
    except RuntimeError:
        if strict:
            raise
try:
    from temporalio import activity, workflow
    from temporalio.exceptions import ApplicationError
except ImportError as exc:
    raise RuntimeError("Install agent-memory-core[temporal]") from exc

def _assert_live_execution_authorization(request: dict) -> None:
    """Reject queued work whose principal/session/membership is no longer valid."""
    principal_id=request.get("principal_id")
    if not principal_id:
        # System-originated work is allowed when the activity is already bound
        # to a restricted PostgreSQL tenant login. User-originated work carries
        # principal/session/membership state and is checked against the control
        # plane below. This keeps machine workflows usable without weakening
        # revocation for browser-originated work.
        if os.getenv("MEMORY_TEMPORAL_REQUIRE_RLS", "0") == "1":
            return
        return
    identity_dsn=os.getenv("MEMORY_IDENTITY_DSN") or _database_dsn()
    from .identity import IdentityStore
    store=IdentityStore(identity_dsn)
    try:
        allowed=store.authorize_background_execution(
            principal_id=principal_id,
            tenant_id=request["tenant_id"],
            session_id=request.get("session_id"),
            credential_id=request.get("credential_id"),
            membership_version=request.get("membership_version"),
        )
    finally:
        store.close()
    if not allowed:
        # Authorization revocation is a terminal business decision, not a
        # transient infrastructure failure. Temporal's default Activity retry
        # policy is unbounded; mark this denial non-retryable or the workflow
        # will remain queued until an external timeout/cancellation.
        raise ApplicationError(
            "Queued work is no longer authorized",
            type="AuthorizationDenied",
            non_retryable=True,
        )

@activity.defn(name="verify_memory_record")
def verify_memory_record(request: dict) -> dict:
    from .postgres import PostgresMemory
    db=PostgresMemory(_database_dsn())
    try:
        _assert_temporal_tenant(db, request["tenant_id"])
        _assert_live_execution_authorization(request)
        row=db.conn.execute(
            """SELECT id::text,subject_id,predicate,source_id,superseded_at
               FROM memory_claims WHERE tenant_id=%s AND id=%s::uuid""",
            (request["tenant_id"],request["memory_id"])).fetchone()
        if row is None:
            raise ValueError("Claim absent in authorized tenant")
        return {
            "tenant_id": request["tenant_id"],
            "memory_id": row[0],
            "subject_id": row[1],
            "predicate": row[2],
            "source_id": row[3],
            "active": row[4] is None,
            # Carry the authorization snapshot through the workflow so every
            # side-effecting activity can re-authorize immediately before it
            # commits a durable effect.
            "principal_id": request.get("principal_id"),
            "session_id": request.get("session_id"),
            "credential_id": request.get("credential_id"),
            "membership_version": request.get("membership_version"),
        }
    finally:
        db.close()

@activity.defn(name="write_memory_verification_receipt")
def write_memory_verification_receipt(verified: dict) -> dict:
    import json
    from .postgres import PostgresMemory
    receipt_id=uuid5(NAMESPACE_URL,
        "memory-verify:"+verified["tenant_id"]+":"+verified["memory_id"])
    db=PostgresMemory(_database_dsn())
    try:
        _assert_temporal_tenant(db, verified["tenant_id"])
        _assert_live_execution_authorization(verified)
        with db.conn.transaction():
            db.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'claim',%s,'memory.claim.verified',%s::jsonb)
                   ON CONFLICT(event_id) DO NOTHING""",
                (receipt_id,verified["tenant_id"],verified["memory_id"],
                 json.dumps(verified,sort_keys=True)))
        return {"receipt_id":str(receipt_id),**verified}
    finally:
        db.close()

@workflow.defn
class VerifyMemoryWorkflow:
    """Durable verification with queryable progress and cooperative pause/resume.

    Pause takes effect at stage boundaries; it does not interrupt a database
    transaction or an Activity already in flight. Temporal cancellation remains
    the separate mechanism for stopping an execution.
    """
    def __init__(self):
        self._paused = False
        self._stage = "QUEUED"
        self._completed_steps = 0
        self._total_steps = 2
        self._failure = None

    @workflow.signal
    def pause(self) -> None:
        self._paused = True

    @workflow.signal
    def resume(self) -> None:
        self._paused = False

    @workflow.query
    def progress(self) -> dict:
        return {
            "stage": self._stage,
            "paused": self._paused,
            "completed_steps": self._completed_steps,
            "total_steps": self._total_steps,
            "progress_percent": int(self._completed_steps * 100 / self._total_steps),
            "failure": self._failure,
        }

    async def _pause_gate(self) -> None:
        if self._paused:
            self._stage = "PAUSED"
            await workflow.wait_condition(lambda: not self._paused)

    @workflow.run
    async def run(self, request: dict) -> dict:
        try:
            await self._pause_gate()
            self._stage = "VERIFYING_MEMORY"
            verified=await workflow.execute_activity(
                "verify_memory_record",request,start_to_close_timeout=timedelta(seconds=20))
            self._completed_steps = 1
            await self._pause_gate()
            self._stage = "WRITING_RECEIPT"
            result=await workflow.execute_activity(
                "write_memory_verification_receipt",verified,
                start_to_close_timeout=timedelta(seconds=20))
            self._completed_steps = 2
            self._stage = "COMPLETED"
            return result
        except Exception as exc:
            self._failure = type(exc).__name__
            self._stage = "FAILED"
            raise
