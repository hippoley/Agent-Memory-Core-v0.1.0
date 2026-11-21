"""Test-only Provider-side AMC fencing admission.

When enabled, the simulated Provider checks the actual PostgreSQL owner and
keeps the task row locked through its independent SQLite effect commit. This
closes the local-check-to-POST gap in the controlled test, but is NOT a
production cross-database atomic transaction or a general remote API contract.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os


@contextmanager
def provider_fence(event, headers):
    if os.environ.get("PROVIDER_REQUIRE_AMC_FENCE") != "1":
        yield True
        return
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.worker_leases import DurableTaskLeaseStore, LeaseConflict

    tenant = headers.get("X-AMC-Tenant")
    task_id = headers.get("X-AMC-Task")
    worker = headers.get("X-AMC-Worker")
    try:
        epoch = int(headers.get("X-AMC-Epoch", ""))
    except (ValueError, TypeError):
        yield False
        return
    if not all([tenant, task_id, worker]) or epoch <= 0:
        yield False
        return
    db = PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        with db.conn.transaction():
            try:
                # FOR UPDATE serializes ownership change with Provider commit.
                DurableTaskLeaseStore(db.conn).assert_current(
                    tenant_id=tenant, task_id=task_id,
                    worker_id=worker, lease_epoch=epoch, lock=True)
            except LeaseConflict:
                yield False
                return
            canonical = json.dumps(event, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False)
            digest = hashlib.sha256(canonical.encode()).hexdigest()
            row = db.conn.execute(
                """SELECT e.request_sha256,e.request,e.status,
                          e.execution_worker_id,e.execution_lease_epoch
                   FROM memory_task_effects e
                   JOIN memory_durable_tasks t ON t.task_id=e.task_id
                   WHERE t.tenant_id=%s AND e.task_id=%s::uuid
                     AND e.effect_key=%s FOR UPDATE""",
                (tenant,task_id,event.get("effect_key")),
            ).fetchone()
            if (not row or row[0] != digest or row[1] != event
                    or row[2] != "executing" or row[3] != worker or row[4] != epoch):
                yield False
                return
            yield True
    finally:
        db.close()
