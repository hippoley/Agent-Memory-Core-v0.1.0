"""Independent PostgreSQL-backed durable business-task worker runtime.

Handlers are application code. The runtime owns claim, heartbeat, fenced
checkpoint commits, completion, and recovery from a previously committed
checkpoint. It deliberately does not promise exactly-once external effects:
handlers must use durable effect keys and provider-side idempotency/reconcile.
"""
from __future__ import annotations

import importlib
import logging
import os
import signal
import threading
import time
from typing import Callable
from uuid import uuid4

from .worker_leases import DurableTaskLeaseStore, LeaseConflict

log = logging.getLogger(__name__)


class WorkerContext:
    """Fenced capabilities supplied to one handler invocation."""

    def __init__(self, *, connection_factory: Callable, tenant_id: str,
                 task: dict, worker_id: str, lease_seconds: int,
                 lease_lost: threading.Event | None = None):
        self.connection_factory = connection_factory
        self.tenant_id = tenant_id
        self.task = task
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.lease_lost = lease_lost or threading.Event()
        self.lease_epoch = int(task["lease_epoch"])
        self.checkpoint_version = int(task["checkpoint_version"])
        self.checkpoint = task.get("checkpoint")

    def assert_lease(self, *, reconciliation_only: bool = False) -> None:
        """Check fencing; restrict writes, while permitting read-only UNKNOWN lookup.

        reconciliation_only MUST only be used for trusted Provider.reconcile()
        lookups. It never grants execute() or task-completion authority.
        """
        if self.lease_lost.is_set():
            raise LeaseConflict("Worker heartbeat reports that the lease was lost")
        db = self.connection_factory()
        try:
            DurableTaskLeaseStore(db.conn).assert_current(
                tenant_id=self.tenant_id,task_id=self.task["task_id"],
                worker_id=self.worker_id,lease_epoch=self.lease_epoch)
            # The same canonical policy guards BOTH execution and final
            # durable task completion, including direct HTTP/SDK writes.
            from .durable_tasks import AUTHORIZED_CONTINUITY_TASK_TYPE
            if (self.task["task_type"] == AUTHORIZED_CONTINUITY_TASK_TYPE
                    and not reconciliation_only):
                from .worker_identity import assert_current_task_creator_write_authority
                assert_current_task_creator_write_authority(
                    db.conn, tenant_id=self.tenant_id,
                    task_id=self.task["task_id"])
        finally:
            db.close()

    def list_effects(self) -> list[dict]:
        """Read this task's effect ledger to decide whether to execute or reconcile."""
        from .durable_effects import DurableTaskEffectStore
        db=self.connection_factory()
        try:
            return DurableTaskEffectStore(db.conn).list_effects(
                tenant_id=self.tenant_id,task_id=self.task["task_id"])
        finally:
            db.close()

    def execute_effect(self, *, effect_key: str, provider) -> dict:
        """Execute one requested effect using provider idempotency; ambiguity stays UNKNOWN."""
        from .durable_effects import DurableTaskEffectRuntime
        return DurableTaskEffectRuntime(self.connection_factory).execute(
            context=self,effect_key=effect_key,provider=provider)

    def reconcile_effect(self, *, effect_key: str, provider) -> dict:
        """Reconcile an UNKNOWN effect; NOT_FOUND is the only path back to requested."""
        from .durable_effects import DurableTaskEffectRuntime
        return DurableTaskEffectRuntime(self.connection_factory).reconcile(
            context=self,effect_key=effect_key,provider=provider)

    def commit_checkpoint(self, *, checkpoint: dict, idempotency_key: str,
                          progress_completed: int = 0, progress_total: int = 0,
                          artifact_sha256: str | None = None,
                          effect_key: str | None = None,
                          effect_request: dict | None = None) -> dict:
        db = self.connection_factory()
        try:
            result = DurableTaskLeaseStore(db.conn).commit_checkpoint(
                tenant_id=self.tenant_id, task_id=self.task["task_id"],
                worker_id=self.worker_id, lease_epoch=self.lease_epoch,
                expected_version=self.checkpoint_version, checkpoint=checkpoint,
                idempotency_key=idempotency_key,
                progress_completed=progress_completed, progress_total=progress_total,
                artifact_sha256=artifact_sha256, effect_key=effect_key,
                effect_request=effect_request,
            )
            self.checkpoint_version = int(result["version"])
            self.checkpoint = checkpoint
            return result
        finally:
            db.close()


class DurableTaskWorker:
    """A single worker process; run multiple instances for competing consumers."""

    def __init__(self, *, connection_factory: Callable, tenant_id: str,
                 handlers: dict[str, Callable], worker_id: str | None = None,
                 lease_seconds: int = 30, heartbeat_interval: float | None = None):
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        self.connection_factory = connection_factory
        self.tenant_id = tenant_id
        self.handlers = dict(handlers)
        self.worker_id = worker_id or f"worker-{uuid4()}"
        self.lease_seconds = lease_seconds
        self.heartbeat_interval = heartbeat_interval or max(0.5, lease_seconds / 3)
        if self.heartbeat_interval <= 0 or self.heartbeat_interval >= lease_seconds:
            raise ValueError("heartbeat_interval must be positive and shorter than the lease")

    def _claim(self, task_id: str | None = None) -> dict | None:
        db = self.connection_factory()
        try:
            return DurableTaskLeaseStore(db.conn).claim(
                tenant_id=self.tenant_id, worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,task_id=task_id)
        finally:
            db.close()

    def _finish(self, task: dict, *, status: str, result=None, error=None) -> None:
        db = self.connection_factory()
        try:
            DurableTaskLeaseStore(db.conn).finish(
                tenant_id=self.tenant_id, task_id=task["task_id"],
                worker_id=self.worker_id, lease_epoch=task["lease_epoch"],
                status=status, result=result, error=error)
        finally:
            db.close()

    def run_once(self, task_id: str | None = None) -> bool:
        task = self._claim(task_id=task_id)
        if task is None:
            return False
        lost = threading.Event()
        stop = threading.Event()

        def heartbeat():
            while not stop.wait(self.heartbeat_interval):
                db = self.connection_factory()
                try:
                    renewed = DurableTaskLeaseStore(db.conn).renew(
                        tenant_id=self.tenant_id, task_id=task["task_id"],
                        worker_id=self.worker_id, lease_epoch=task["lease_epoch"],
                        lease_seconds=self.lease_seconds)
                    if not renewed:
                        lost.set()
                        log.error("lease lost task=%s epoch=%s", task["task_id"], task["lease_epoch"])
                        return
                except Exception:
                    lost.set()
                    log.exception("heartbeat failed task=%s", task["task_id"])
                    return
                finally:
                    db.close()

        thread = threading.Thread(target=heartbeat, name="durable-task-heartbeat", daemon=True)
        thread.start()
        context = WorkerContext(
            connection_factory=self.connection_factory, tenant_id=self.tenant_id,
            task=task, worker_id=self.worker_id, lease_seconds=self.lease_seconds,
            lease_lost=lost)
        try:
            # Any effect still marked executing under an older lease epoch has
            # an ambiguous remote outcome. Preserve it as UNKNOWN; never retry
            # the provider call until its reconciliation contract says NOT_FOUND.
            db = self.connection_factory()
            try:
                from .durable_effects import DurableTaskEffectStore
                DurableTaskEffectStore(db.conn).mark_stale_executions_unknown(
                    tenant_id=self.tenant_id,task_id=task["task_id"],
                    worker_id=self.worker_id,lease_epoch=task["lease_epoch"])
            finally:
                db.close()
            # A strict task must fail before any user handler code runs.
            # The same check is repeated inside ctx.execute_effect before
            # the outbound provider call, covering mid-task revocations.
            from .durable_tasks import AUTHORIZED_CONTINUITY_TASK_TYPE
            if task["task_type"] == AUTHORIZED_CONTINUITY_TASK_TYPE:
                context.assert_lease()
            handler = self.handlers.get(task["task_type"])
            if handler is None:
                raise LookupError(f"No handler registered for task_type={task['task_type']!r}")
            result = handler(task, context)
            if lost.is_set():
                raise LeaseConflict("Worker lost its lease while the handler was running")
            stop.set()
            thread.join(timeout=max(1.0, self.heartbeat_interval * 2))
            if task["task_type"] == AUTHORIZED_CONTINUITY_TASK_TYPE:
                context.assert_lease()
            self._finish(task, status="completed", result=result or {})
        except LeaseConflict:
            log.exception("task execution fenced task=%s", task["task_id"])
            raise
        except Exception as exc:
            log.exception("task handler failed task=%s", task["task_id"])
            if not lost.is_set():
                stop.set()
                thread.join(timeout=max(1.0, self.heartbeat_interval * 2))
                try:
                    self._finish(task, status="failed",
                                 error={"type": type(exc).__name__, "message": str(exc)[:2000]})
                except LeaseConflict:
                    log.exception("failure status rejected because worker was fenced")
            raise
        finally:
            stop.set()
            thread.join(timeout=max(1.0, self.heartbeat_interval * 2))
        return True

    def run_forever(self, *, poll_interval: float = 1.0,
                    stop_event: threading.Event | None = None) -> None:
        if poll_interval < 0.05:
            raise ValueError("poll_interval must be at least 50ms")
        stop = stop_event or threading.Event()
        while not stop.is_set():
            try:
                if not self.run_once():
                    stop.wait(poll_interval)
            except LeaseConflict:
                # A newer epoch owns the task. Never attempt a compensating write.
                stop.wait(poll_interval)
            except Exception:
                log.exception("worker loop iteration failed")
                stop.wait(poll_interval)


def main() -> None:
    """Run a standalone worker process configured entirely by environment."""
    from .postgres import PostgresMemory
    from .worker_identity import assert_worker_tenant, configured_worker_tenant

    dsn=os.getenv("MEMORY_DSN")
    tenant_id=configured_worker_tenant()
    handler_module=os.getenv("MEMORY_WORKER_HANDLER_MODULE")
    if not dsn or not tenant_id or not handler_module:
        raise SystemExit(
            "MEMORY_DSN, MEMORY_WORKER_TENANT and MEMORY_WORKER_HANDLER_MODULE are required")
    module=importlib.import_module(handler_module)
    handlers=getattr(module,"HANDLERS",None)
    if not isinstance(handlers,dict) or not handlers:
        raise SystemExit("Worker handler module must export a non-empty HANDLERS dict")

    def connect():
        return PostgresMemory(dsn)

    probe=connect()
    try:
        assert_worker_tenant(probe,tenant_id)
    finally:
        probe.close()

    worker=DurableTaskWorker(
        connection_factory=connect,tenant_id=tenant_id,handlers=handlers,
        worker_id=os.getenv("MEMORY_WORKER_ID") or None,
        lease_seconds=int(os.getenv("MEMORY_WORKER_LEASE_SECONDS","30")),
    )
    stop=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    worker.run_forever(
        poll_interval=float(os.getenv("MEMORY_WORKER_POLL_SECONDS","1")),
        stop_event=stop,
    )


if __name__ == "__main__":
    main()
