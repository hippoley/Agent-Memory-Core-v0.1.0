"""Strict creator-authorized task profile; real PostgreSQL negative path."""
import os
from uuid import uuid4
import pytest

from agent_memory_core.durable_tasks import (
    AUTHORIZED_CONTINUITY_TASK_TYPE, required_effect_keys)

def test_authorized_profile_requires_effect_manifest():
    assert required_effect_keys(
        AUTHORIZED_CONTINUITY_TASK_TYPE,
        {"required_effect_keys": ["set-bedroom-v1"]}) == ("set-bedroom-v1",)


@pytest.mark.integration
def test_creator_revocation_blocks_db_worker_before_handler_and_effect():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.identity import IdentityStore
    from agent_memory_core.durable_tasks import DurableTaskStore
    from agent_memory_core.worker_runtime import DurableTaskWorker, WorkerContext
    from agent_memory_core.worker_leases import LeaseConflict

    bootstrap = PostgresMemory(dsn)
    try:
        bootstrap.migrate()
    finally:
        bootstrap.close()
    identity = IdentityStore(dsn)
    owner = identity.create_principal(principal_type="service", display_name="author")
    second = identity.create_principal(principal_type="service", display_name="other-owner")
    tenant = "c02-worker-auth-" + uuid4().hex
    identity.create_tenant(tenant_id=tenant, display_name="auth test",
                           owner_principal_id=owner)
    identity.add_membership(tenant_id=tenant, principal_id=second, role="owner")
    identity.close()

    def connect():
        return PostgresMemory(dsn)
    db = connect()
    try:
        with pytest.raises(ValueError, match="authenticated task creator"):
            DurableTaskStore(db.conn).create_task(
                tenant_id=tenant, task_type=AUTHORIZED_CONTINUITY_TASK_TYPE,
                input={"required_effect_keys": ["set-bedroom-v1"]})
        task = DurableTaskStore(db.conn).create_task(
            tenant_id=tenant, task_type=AUTHORIZED_CONTINUITY_TASK_TYPE,
            created_by_principal_id=owner,
            input={"required_effect_keys": ["set-bedroom-v1"]},
            idempotency_key="authorized-" + uuid4().hex)
    finally:
        db.close()

    seen = []
    worker = DurableTaskWorker(
        connection_factory=connect, tenant_id=tenant,
        handlers={AUTHORIZED_CONTINUITY_TASK_TYPE: lambda task, ctx:
                  seen.append("handler-ran")},
        worker_id="restricted-worker", heartbeat_interval=5)

    # Check the still-authorized Principal is allowed to enter the handler
    # boundary, without actually calling a physical effect.
    claimed = worker._claim(task_id=task["task_id"])
    assert claimed is not None
    ctx = WorkerContext(connection_factory=connect, tenant_id=tenant,
                        task=claimed, worker_id="restricted-worker",
                        lease_seconds=30)
    ctx.assert_lease()

    identity = IdentityStore(dsn)
    try:
        identity.set_membership_role(
            tenant_id=tenant, principal_id=owner, role="reader")
    finally:
        identity.close()
    with pytest.raises(LeaseConflict, match="no longer authorized"):
        ctx.assert_lease()

    # Expire the old lease using DB time, then try a fresh worker epoch.
    db = connect()
    try:
        db.conn.execute(
            """UPDATE memory_durable_tasks
               SET lease_expires_at=clock_timestamp()-interval '1 second'
               WHERE tenant_id=%s AND task_id=%s::uuid""",
            (tenant, task["task_id"]))
    finally:
        db.close()
    with pytest.raises(LeaseConflict, match="no longer authorized"):
        worker.run_once(task_id=task["task_id"])
    assert seen == [], "Revoked task must not enter application handler"
