"""Real PostgreSQL acceptance for external effect ambiguity and reconciliation."""
import os
from uuid import uuid4

import pytest

pytestmark=pytest.mark.integration


def _setup_task(db,tenant):
    from agent_memory_core.durable_tasks import DurableTaskStore
    task=DurableTaskStore(db.conn).create_task(
        tenant_id=tenant,task_type="test.external.effect",
        input={"operation":"write-record"},idempotency_key="effect-task-"+uuid4().hex)
    return task["task_id"]


def _claim(db,tenant,worker):
    from agent_memory_core.worker_leases import DurableTaskLeaseStore
    return DurableTaskLeaseStore(db.conn).claim(
        tenant_id=tenant,worker_id=worker,lease_seconds=30)


def _expire(db,tenant,task_id):
    db.conn.execute(
        """UPDATE memory_durable_tasks
           SET lease_expires_at=clock_timestamp()-interval '1 second'
           WHERE tenant_id=%s AND task_id=%s::uuid""",(tenant,task_id))


def test_remote_success_lost_ack_becomes_unknown_and_reconciles_without_reexecution():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    pytest.importorskip("psycopg")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.worker_leases import DurableTaskLeaseStore
    from agent_memory_core.durable_tasks import DurableTaskStore
    from agent_memory_core.durable_effects import (
        DurableTaskEffectStore,DurableTaskEffectRuntime,provider_idempotency_key)
    from agent_memory_core.worker_runtime import WorkerContext

    tenant="effect-unknown-"+uuid4().hex
    task_id=None
    db=PostgresMemory(dsn)
    try:
        db.migrate()
        db.migrate()  # Migration 008 must remain safe under package-wide reapplication.
        task_id=_setup_task(db,tenant)
        a=_claim(db,tenant,"effect-worker-A")
        assert a and a["task_id"]==task_id
        DurableTaskLeaseStore(db.conn).commit_checkpoint(
            tenant_id=tenant,task_id=task_id,worker_id="effect-worker-A",
            lease_epoch=a["lease_epoch"],expected_version=0,
            checkpoint={"cursor":1},idempotency_key="effect-intent-checkpoint",
            effect_key="remote-write-1",
            effect_request={"provider":"fake-ledger","operation":"create","value":42})
        intent=DurableTaskEffectStore(db.conn).begin_execution(
            tenant_id=tenant,task_id=task_id,effect_key="remote-write-1",
            worker_id="effect-worker-A",lease_epoch=a["lease_epoch"])
        assert intent["attempt_count"]==1
        assert intent["idempotency_key"]==provider_idempotency_key(
            tenant_id=tenant,task_id=task_id,effect_key="remote-write-1")

        # Simulate the hard failure window: the provider commits, then Worker A
        # dies before PostgreSQL can record the acknowledgement.
        remote={}
        execute_calls=[]
        from agent_memory_core.provider_contract import ProviderCapabilities
        class FakeProvider:
            capabilities=ProviderCapabilities(
                idempotency=True,reconciliation=True,lookup_consistency="strong",
                idempotency_retention_seconds=86400)
            def execute(self,request,*,idempotency_key):
                execute_calls.append(idempotency_key)
                remote[idempotency_key]={"provider_reference":"remote-42","result":{"created":True}}
                return remote[idempotency_key]
            def reconcile(self,request,*,idempotency_key,provider_reference):
                if idempotency_key in remote:
                    value=remote[idempotency_key]
                    return {"outcome":"committed",**value}
                return {"outcome":"not_found"}

        provider=FakeProvider()
        remote[intent["idempotency_key"]]=provider.execute(
            intent["request"],idempotency_key=intent["idempotency_key"])
        # No mark_committed call: this is the lost-ack crash point.
        _expire(db,tenant,task_id)
        b=_claim(db,tenant,"effect-worker-B")
        assert b and b["lease_epoch"]>a["lease_epoch"]

        effects=DurableTaskEffectStore(db.conn)
        assert effects.mark_stale_executions_unknown(
            tenant_id=tenant,task_id=task_id,worker_id="effect-worker-B",
            lease_epoch=b["lease_epoch"])==1
        assert effects.get_effect(task_id=task_id,effect_key="remote-write-1")["status"]=="unknown"

        context=WorkerContext(connection_factory=lambda:PostgresMemory(dsn),
            tenant_id=tenant,task=b,worker_id="effect-worker-B",lease_seconds=30)
        runtime=DurableTaskEffectRuntime(lambda:PostgresMemory(dsn))
        outcome=runtime.reconcile(context=context,effect_key="remote-write-1",provider=provider)
        assert outcome["outcome"]=="committed"
        final=effects.get_effect(task_id=task_id,effect_key="remote-write-1")
        assert final["status"]=="committed"
        assert final["provider_reference"]=="remote-42"
        assert final["provider_result"]=={"created":True}
        assert len(execute_calls)==1, "recovery must reconcile, not re-execute"
        DurableTaskLeaseStore(db.conn).finish(
            tenant_id=tenant,task_id=task_id,worker_id="effect-worker-B",
            lease_epoch=b["lease_epoch"],status="completed",result={"done":True})
        assert DurableTaskStore(db.conn).get_task(
            tenant_id=tenant,task_id=task_id)["status"]=="completed"
    finally:
        db.close()


def test_unknown_effect_cannot_be_reexecuted_until_provider_says_not_found():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.worker_leases import DurableTaskLeaseStore
    from agent_memory_core.durable_effects import DurableTaskEffectStore,EffectConflict

    tenant="effect-retry-"+uuid4().hex
    db=PostgresMemory(dsn)
    try:
        db.migrate()
        task_id=_setup_task(db,tenant)
        a=_claim(db,tenant,"effect-retry-A")
        DurableTaskLeaseStore(db.conn).commit_checkpoint(
            tenant_id=tenant,task_id=task_id,worker_id="effect-retry-A",
            lease_epoch=a["lease_epoch"],expected_version=0,
            checkpoint={"cursor":1},idempotency_key="retry-intent-checkpoint",
            effect_key="retry-after-reconcile",
            effect_request={"provider":"fake-ledger","operation":"create","value":7})
        effects=DurableTaskEffectStore(db.conn)
        effects.begin_execution(tenant_id=tenant,task_id=task_id,
            effect_key="retry-after-reconcile",worker_id="effect-retry-A",
            lease_epoch=a["lease_epoch"])
        _expire(db,tenant,task_id)
        b=_claim(db,tenant,"effect-retry-B")
        effects.mark_stale_executions_unknown(tenant_id=tenant,task_id=task_id,
            worker_id="effect-retry-B",lease_epoch=b["lease_epoch"])
        with pytest.raises(EffectConflict,match="unknown"):
            effects.begin_execution(tenant_id=tenant,task_id=task_id,
                effect_key="retry-after-reconcile",worker_id="effect-retry-B",
                lease_epoch=b["lease_epoch"])
        effects.begin_reconciliation(tenant_id=tenant,task_id=task_id,
            effect_key="retry-after-reconcile",worker_id="effect-retry-B",
            lease_epoch=b["lease_epoch"])
        # A reconciler can die too. Its in-progress lookup is also ambiguous.
        _expire(db,tenant,task_id)
        c=_claim(db,tenant,"effect-retry-C")
        assert c and c["lease_epoch"]>b["lease_epoch"]
        assert effects.mark_stale_executions_unknown(tenant_id=tenant,
            task_id=task_id,worker_id="effect-retry-C",
            lease_epoch=c["lease_epoch"])==1
        assert effects.get_effect(task_id=task_id,
            effect_key="retry-after-reconcile")["status"]=="unknown"
        effects.begin_reconciliation(tenant_id=tenant,task_id=task_id,
            effect_key="retry-after-reconcile",worker_id="effect-retry-C",
            lease_epoch=c["lease_epoch"])
        effects.resolve_reconciliation(tenant_id=tenant,task_id=task_id,
            effect_key="retry-after-reconcile",worker_id="effect-retry-C",
            lease_epoch=c["lease_epoch"],outcome="not_found")
        retry=effects.begin_execution(tenant_id=tenant,task_id=task_id,
            effect_key="retry-after-reconcile",worker_id="effect-retry-C",
            lease_epoch=c["lease_epoch"])
        assert retry["attempt_count"]==2
        assert effects.get_effect(task_id=task_id,
            effect_key="retry-after-reconcile")["status"]=="executing"
    finally:
        db.close()


def test_completed_task_is_rejected_while_effect_is_unknown():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.worker_leases import DurableTaskLeaseStore,LeaseConflict
    from agent_memory_core.durable_effects import DurableTaskEffectStore

    tenant="effect-completion-gate-"+uuid4().hex
    db=PostgresMemory(dsn)
    try:
        db.migrate()
        task_id=_setup_task(db,tenant)
        a=_claim(db,tenant,"effect-gate-A")
        DurableTaskLeaseStore(db.conn).commit_checkpoint(
            tenant_id=tenant,task_id=task_id,worker_id="effect-gate-A",
            lease_epoch=a["lease_epoch"],expected_version=0,
            checkpoint={"cursor":1},idempotency_key="gate-checkpoint",
            effect_key="must-reconcile",effect_request={"operation":"remote"})
        with pytest.raises(LeaseConflict,match="external effect"):
            DurableTaskLeaseStore(db.conn).finish(
                tenant_id=tenant,task_id=task_id,worker_id="effect-gate-A",
                lease_epoch=a["lease_epoch"],status="completed",result={})
    finally:
        db.close()
