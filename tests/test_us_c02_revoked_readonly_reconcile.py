"""US-C02: revoked creator blocks new effects but not read-only UNKNOWN lookup."""
import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def test_revocation_preserves_fenced_unknown_reconciliation_without_reexecution():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.identity import IdentityStore
    from agent_memory_core.durable_tasks import DurableTaskStore, AUTHORIZED_CONTINUITY_TASK_TYPE
    from agent_memory_core.worker_leases import DurableTaskLeaseStore, LeaseConflict
    from agent_memory_core.worker_runtime import WorkerContext
    from agent_memory_core.provider_contract import ProviderCapabilities

    db = PostgresMemory(dsn)
    try:
        db.migrate()
    finally:
        db.close()

    identity = IdentityStore(dsn)
    owner = identity.create_principal(principal_type="service", display_name="C02 owner")
    second = identity.create_principal(principal_type="service", display_name="C02 co-owner")
    tenant = "c02-reconcile-revoked-" + uuid4().hex
    identity.create_tenant(tenant_id=tenant, display_name="C02", owner_principal_id=owner)
    identity.add_membership(tenant_id=tenant, principal_id=second, role="owner")
    identity.close()

    def connect():
        return PostgresMemory(dsn)

    db = connect()
    try:
        task = DurableTaskStore(db.conn).create_task(
            tenant_id=tenant, task_type=AUTHORIZED_CONTINUITY_TASK_TYPE,
            created_by_principal_id=owner,
            input={"required_effect_keys": ["first", "second"]},
            idempotency_key="revoked-reconcile-" + uuid4().hex)
        claimed = DurableTaskLeaseStore(db.conn).claim(
            tenant_id=tenant, task_id=task["task_id"], worker_id="recovery-worker")
    finally:
        db.close()

    ctx = WorkerContext(connection_factory=connect, tenant_id=tenant,
                        task=claimed, worker_id="recovery-worker", lease_seconds=30)
    ctx.assert_lease()
    ctx.commit_checkpoint(
        checkpoint={"phase": "first"}, idempotency_key="first-checkpoint",
        effect_key="first", effect_request={"device": "synthetic", "value": 26})
    ctx.commit_checkpoint(
        checkpoint={"phase": "second"}, idempotency_key="second-checkpoint",
        effect_key="second", effect_request={"device": "synthetic", "value": 25})

    executed, reconciled = [], []
    class Provider:
        capabilities = ProviderCapabilities(
            idempotency=True, reconciliation=True, lookup_consistency="strong",
            idempotency_retention_seconds=86400)
        def execute(self, request, *, idempotency_key):
            executed.append(idempotency_key)
            raise TimeoutError("remote commit, response ACK lost")
        def reconcile(self, request, *, idempotency_key, provider_reference):
            reconciled.append(idempotency_key)
            assert request["value"] == 26
            return {"outcome": "committed",
                    "provider_reference": "lookup-confirmed-original-command",
                    "result": {"original_value": 26}}

    provider = Provider()
    first = ctx.execute_effect(effect_key="first", provider=provider)
    assert first["status"] == "unknown"
    assert len(executed) == 1

    identity = IdentityStore(dsn)
    try:
        identity.set_membership_role(tenant_id=tenant, principal_id=owner, role="reader")
    finally:
        identity.close()

    with pytest.raises(LeaseConflict, match="no longer authorized"):
        ctx.execute_effect(effect_key="second", provider=provider)
    assert len(executed) == 1, "Revocation must block a new device command"

    # Only a reconciliation LOOKUP is allowed: the original UNKNOWN effect
    # may be resolved without granting any new execution authority.
    outcome = ctx.reconcile_effect(effect_key="first", provider=provider)
    assert outcome["outcome"] == "committed"
    assert len(executed) == 1
    assert reconciled == executed

    db = connect()
    try:
        effects = {item["effect_key"]: item for item in
                   __import__("agent_memory_core.durable_effects",
                              fromlist=["DurableTaskEffectStore"]).DurableTaskEffectStore(
                       db.conn).list_effects(tenant_id=tenant, task_id=task["task_id"])}
        assert effects["first"]["status"] == "committed"
        assert effects["first"]["attempt_count"] == 1
        assert effects["second"]["status"] == "requested"
        with pytest.raises(LeaseConflict, match="no longer authorized"):
            DurableTaskLeaseStore(db.conn).finish(
                tenant_id=tenant, task_id=task["task_id"],
                worker_id="recovery-worker", lease_epoch=claimed["lease_epoch"],
                status="completed", result={"falsely_done": True})
        # Read-only reconciliation does not elevate revoked tasks to completed.
        assert DurableTaskStore(db.conn).get_task(
            tenant_id=tenant, task_id=task["task_id"])["status"] == "running"
    finally:
        db.close()
