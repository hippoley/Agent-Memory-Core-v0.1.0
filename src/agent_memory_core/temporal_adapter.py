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


@activity.defn(name="execute_durable_task")
def execute_durable_task(request: dict) -> dict:
    """Run one registered application handler under the PostgreSQL task lease."""
    import importlib
    import threading
    from temporalio import activity
    from temporalio.exceptions import ApplicationError
    from .postgres import PostgresMemory
    from .worker_runtime import DurableTaskWorker

    required=("tenant_id","task_id","workflow_id","task_type")
    if any(not isinstance(request.get(key),str) or not request[key] for key in required):
        raise ApplicationError("Durable task identity is incomplete",
                               type="InvalidTaskIdentity",non_retryable=True)

    dsn=_database_dsn()
    module_name=os.getenv("MEMORY_WORKER_HANDLER_MODULE",
                          "agent_memory_core.example_task_handlers")
    module=importlib.import_module(module_name)
    handlers=getattr(module,"HANDLERS",None)
    if not isinstance(handlers,dict) or request["task_type"] not in handlers:
        raise ApplicationError(
            f"No registered handler for task_type={request['task_type']!r}",
            type="TaskHandlerNotRegistered",non_retryable=True)

    db=PostgresMemory(dsn)
    try:
        _assert_temporal_tenant(db,request["tenant_id"])
        row=db.conn.execute(
            """SELECT task_type,workflow_id,status,result,last_error
               FROM memory_durable_tasks
               WHERE tenant_id=%s AND task_id=%s::uuid""",
            (request["tenant_id"],request["task_id"]),
        ).fetchone()
        if row is None or row[0]!=request["task_type"] or row[1]!=request["workflow_id"]:
            raise ApplicationError("PostgreSQL task identity does not match Temporal request",
                                   type="TaskIdentityMismatch",non_retryable=True)
        if row[2]=="completed":
            return {"status":"completed","result":row[3],"replayed":True}
        if row[2]=="failed":
            raise ApplicationError("PostgreSQL task is already failed",
                                   row[4],type="TaskAlreadyFailed",non_retryable=True)
    finally:
        db.close()

    def connect():
        return PostgresMemory(dsn)

    worker=DurableTaskWorker(
        connection_factory=connect,
        tenant_id=request["tenant_id"],
        handlers=handlers,
        worker_id="temporal-activity:"+request["workflow_id"],
        lease_seconds=int(os.getenv("MEMORY_WORKER_LEASE_SECONDS","30")),
    )
    outcome={"ran":False,"error":None}
    def run():
        try:
            outcome["ran"]=worker.run_once(task_id=request["task_id"])
        except BaseException as exc:
            outcome["error"]=exc

    thread=threading.Thread(target=run,name="durable-task-handler",daemon=True)
    thread.start()
    while thread.is_alive():
        activity.heartbeat({"tenant_id":request["tenant_id"],
                            "task_id":request["task_id"],
                            "workflow_id":request["workflow_id"]})
        thread.join(timeout=10)
    if outcome["error"] is not None:
        exc=outcome["error"]
        raise ApplicationError(
            f"Durable task worker failed: {type(exc).__name__}: {exc}",
            type="DurableTaskExecutionFailed",non_retryable=True) from exc

    db=PostgresMemory(dsn)
    try:
        task=db.conn.execute(
            """SELECT status,result,last_error FROM memory_durable_tasks
               WHERE tenant_id=%s AND task_id=%s::uuid""",
            (request["tenant_id"],request["task_id"]),
        ).fetchone()
    finally:
        db.close()
    if task is None:
        raise ApplicationError("Task disappeared after execution",
                               type="TaskMissingAfterExecution",non_retryable=True)
    if task[0]=="completed":
        return {"status":"completed","result":task[1],"replayed":not outcome["ran"]}
    if task[0]=="failed":
        raise ApplicationError("Task handler marked PostgreSQL task failed",
                               task[2],type="DurableTaskFailed",non_retryable=True)
    if not outcome["ran"]:
        raise ApplicationError("Task is not currently claimable",
                               type="DurableTaskLeaseBusy",non_retryable=False)
    raise ApplicationError("Task runner returned without a terminal task state",
                           type="DurableTaskNotTerminal",non_retryable=False)


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
