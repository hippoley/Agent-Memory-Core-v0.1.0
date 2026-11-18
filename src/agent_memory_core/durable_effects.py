"""Fenced execution and reconciliation for external task effects.

PostgreSQL records intent and local knowledge. It cannot make a remote API
transaction atomic. Ambiguous outcomes remain UNKNOWN until a provider adapter
can reconcile them using the same stable idempotency key.
"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from .worker_leases import DurableTaskLeaseStore, LeaseConflict


class EffectConflict(RuntimeError):
    """Effect state or ownership does not permit the requested transition."""


class ProviderRejected(RuntimeError):
    """Provider definitively rejected the request without performing it."""


def provider_idempotency_key(*, tenant_id: str, task_id: str, effect_key: str) -> str:
    material="\0".join((tenant_id,task_id,effect_key)).encode("utf-8")
    return "amc_"+hashlib.sha256(material).hexdigest()


class DurableTaskEffectStore:
    def __init__(self, conn):
        self.conn=conn

    def _assert_task_lease(self, *, tenant_id, task_id, worker_id, lease_epoch):
        DurableTaskLeaseStore(self.conn).assert_current(
            tenant_id=tenant_id,task_id=task_id,worker_id=worker_id,
            lease_epoch=lease_epoch,lock=True)

    def mark_stale_executions_unknown(self, *, tenant_id: str, task_id: str,
                                      worker_id: str, lease_epoch: int) -> int:
        """Fence old in-flight effects after this process claims a newer task epoch."""
        with self.conn.transaction():
            self._assert_task_lease(tenant_id=tenant_id,task_id=task_id,
                                    worker_id=worker_id,lease_epoch=lease_epoch)
            rows=self.conn.execute(
                """UPDATE memory_task_effects
                   SET status='unknown',execution_worker_id=NULL,
                       execution_lease_epoch=NULL,
                       last_error=%s::jsonb,updated_at=clock_timestamp()
                   WHERE task_id=%s::uuid AND status IN ('executing','reconciling')
                     AND (execution_lease_epoch IS DISTINCT FROM %s
                          OR execution_worker_id IS DISTINCT FROM %s)
                   RETURNING effect_key""",
                (json.dumps({"code":"WORKER_LEASE_LOST",
                             "message":"Previous execution epoch no longer owns the task"},
                            sort_keys=True),task_id,lease_epoch,worker_id),
            ).fetchall()
            for (key,) in rows:
                self.conn.execute(
                    """INSERT INTO memory_outbox
                       (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                       VALUES (%s,%s,'task',%s,'memory.task.effect.unknown',%s::jsonb)""",
                    (uuid4(),tenant_id,task_id,json.dumps({
                        "task_id":task_id,"effect_key":key,
                        "status":"unknown","reason":"WORKER_LEASE_LOST",
                        "new_lease_epoch":lease_epoch},sort_keys=True)),
                )
            return len(rows)

    def begin_execution(self, *, tenant_id: str, task_id: str, effect_key: str,
                        worker_id: str, lease_epoch: int) -> dict:
        with self.conn.transaction():
            self._assert_task_lease(tenant_id=tenant_id,task_id=task_id,
                                    worker_id=worker_id,lease_epoch=lease_epoch)
            row=self.conn.execute(
                """SELECT request,request_sha256,status,attempt_count,
                          provider_reference
                   FROM memory_task_effects
                   WHERE task_id=%s::uuid AND effect_key=%s FOR UPDATE""",
                (task_id,effect_key),
            ).fetchone()
            if row is None:
                raise LookupError("Effect intent not found")
            request,request_hash,status,attempt_count,provider_reference=row
            if status!="requested":
                raise EffectConflict(f"Effect is {status}; execution requires requested")
            if request is None:
                raise EffectConflict("Effect request payload is missing; refusing unsafe execution")
            row2=self.conn.execute(
                """UPDATE memory_task_effects
                   SET status='executing',attempt_count=attempt_count+1,
                       execution_worker_id=%s,execution_lease_epoch=%s,
                       execution_started_at=clock_timestamp(),
                       last_error=NULL,updated_at=clock_timestamp()
                   WHERE task_id=%s::uuid AND effect_key=%s AND status='requested'
                   RETURNING attempt_count""",
                (worker_id,lease_epoch,task_id,effect_key),
            ).fetchone()
            if row2 is None:
                raise EffectConflict("Effect execution claim was concurrently changed")
            return {"task_id":task_id,"effect_key":effect_key,"request":request,
                    "request_sha256":request_hash,"attempt_count":row2[0],
                    "provider_reference":provider_reference,
                    "idempotency_key":provider_idempotency_key(
                        tenant_id=tenant_id,task_id=task_id,effect_key=effect_key)}

    def _transition(self, *, tenant_id: str, task_id: str, effect_key: str,
                    worker_id: str, lease_epoch: int, allowed: tuple[str,...],
                    status: str, provider_reference=None, provider_result=None,
                    error=None) -> None:
        with self.conn.transaction():
            self._assert_task_lease(tenant_id=tenant_id,task_id=task_id,
                                    worker_id=worker_id,lease_epoch=lease_epoch)
            row=self.conn.execute(
                """UPDATE memory_task_effects
                   SET status=%s,provider_reference=coalesce(%s,provider_reference),
                       provider_result=CASE WHEN %s::jsonb IS NULL
                                            THEN provider_result ELSE %s::jsonb END,
                       last_error=%s::jsonb,
                       resolved_at=CASE WHEN %s IN ('committed','failed')
                                        THEN clock_timestamp() ELSE NULL END,
                       execution_worker_id=CASE WHEN %s IN ('unknown','requested','committed','failed')
                                                 THEN NULL ELSE execution_worker_id END,
                       execution_lease_epoch=CASE WHEN %s IN ('unknown','requested','committed','failed')
                                                   THEN NULL ELSE execution_lease_epoch END,
                       updated_at=clock_timestamp()
                   WHERE task_id=%s::uuid AND effect_key=%s
                     AND status = ANY(%s::text[])
                     AND execution_worker_id=%s AND execution_lease_epoch=%s
                   RETURNING effect_key""",
                (status,provider_reference,
                 json.dumps(provider_result,sort_keys=True) if provider_result is not None else None,
                 json.dumps(provider_result,sort_keys=True) if provider_result is not None else None,
                 json.dumps(error,sort_keys=True) if error is not None else None,
                 status,status,status,task_id,effect_key,list(allowed),worker_id,lease_epoch),
            ).fetchone()
            if row is None:
                raise EffectConflict("Effect transition rejected: stale owner, epoch, or state")
            self.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'task',%s,%s,%s::jsonb)""",
                (uuid4(),tenant_id,task_id,"memory.task.effect."+status,
                 json.dumps({"task_id":task_id,"effect_key":effect_key,
                             "status":status,"lease_epoch":lease_epoch,
                             "provider_reference":provider_reference},sort_keys=True)),
            )

    def mark_committed(self, *, tenant_id: str, task_id: str, effect_key: str,
                       worker_id: str, lease_epoch: int, provider_reference: str | None,
                       provider_result: dict) -> None:
        self._transition(tenant_id=tenant_id,task_id=task_id,effect_key=effect_key,
                         worker_id=worker_id,lease_epoch=lease_epoch,
                         allowed=("executing","reconciling"),status="committed",
                         provider_reference=provider_reference,provider_result=provider_result)

    def mark_unknown(self, *, tenant_id: str, task_id: str, effect_key: str,
                     worker_id: str, lease_epoch: int, error: dict) -> None:
        self._transition(tenant_id=tenant_id,task_id=task_id,effect_key=effect_key,
                         worker_id=worker_id,lease_epoch=lease_epoch,
                         allowed=("executing",),status="unknown",error=error)

    def mark_failed(self, *, tenant_id: str, task_id: str, effect_key: str,
                    worker_id: str, lease_epoch: int, error: dict) -> None:
        self._transition(tenant_id=tenant_id,task_id=task_id,effect_key=effect_key,
                         worker_id=worker_id,lease_epoch=lease_epoch,
                         allowed=("executing","reconciling"),status="failed",error=error)

    def begin_reconciliation(self, *, tenant_id: str, task_id: str, effect_key: str,
                             worker_id: str, lease_epoch: int) -> dict:
        with self.conn.transaction():
            self._assert_task_lease(tenant_id=tenant_id,task_id=task_id,
                                    worker_id=worker_id,lease_epoch=lease_epoch)
            row=self.conn.execute(
                """UPDATE memory_task_effects
                   SET status='reconciling',execution_worker_id=%s,
                       execution_lease_epoch=%s,updated_at=clock_timestamp()
                   WHERE task_id=%s::uuid AND effect_key=%s AND status='unknown'
                   RETURNING request,request_sha256,provider_reference,attempt_count""",
                (worker_id,lease_epoch,task_id,effect_key),
            ).fetchone()
            if row is None:
                raise EffectConflict("Only UNKNOWN effects can enter reconciliation")
            request,request_hash,provider_reference,attempt_count=row
            if request is None:
                raise EffectConflict("Effect request payload is missing; cannot reconcile")
            return {"task_id":task_id,"effect_key":effect_key,"request":request,
                    "request_sha256":request_hash,"provider_reference":provider_reference,
                    "attempt_count":attempt_count,
                    "idempotency_key":provider_idempotency_key(
                        tenant_id=tenant_id,task_id=task_id,effect_key=effect_key)}

    def resolve_reconciliation(self, *, tenant_id: str, task_id: str, effect_key: str,
                               worker_id: str, lease_epoch: int, outcome: str,
                               provider_reference: str | None = None,
                               provider_result: dict | None = None,
                               error: dict | None = None) -> None:
        if outcome not in {"committed","not_found","unknown","failed"}:
            raise ValueError("outcome must be committed, not_found, unknown, or failed")
        status={"not_found":"requested"}.get(outcome,outcome)
        self._transition(tenant_id=tenant_id,task_id=task_id,effect_key=effect_key,
                         worker_id=worker_id,lease_epoch=lease_epoch,
                         allowed=("reconciling",),status=status,
                         provider_reference=provider_reference,
                         provider_result=provider_result,error=error)

    def get_effect(self, *, task_id: str, effect_key: str) -> dict | None:
        row=self.conn.execute(
            """SELECT effect_key,request,request_sha256,status,attempt_count,
                      execution_worker_id,execution_lease_epoch,provider_reference,
                      provider_result,last_error,created_at,updated_at,resolved_at
               FROM memory_task_effects WHERE task_id=%s::uuid AND effect_key=%s""",
            (task_id,effect_key),
        ).fetchone()
        if row is None:
            return None
        keys=("effect_key","request","request_sha256","status","attempt_count",
              "execution_worker_id","execution_lease_epoch","provider_reference",
              "provider_result","last_error","created_at","updated_at","resolved_at")
        return dict(zip(keys,row))


class DurableTaskEffectRuntime:
    """Execute/reconcile through a provider's idempotency and lookup contract."""

    def __init__(self, connection_factory):
        self.connection_factory=connection_factory

    def execute(self, *, context, effect_key: str, provider) -> dict:
        context.assert_lease()
        db=self.connection_factory()
        try:
            effect=DurableTaskEffectStore(db.conn).begin_execution(
                tenant_id=context.tenant_id,task_id=context.task["task_id"],
                effect_key=effect_key,worker_id=context.worker_id,
                lease_epoch=context.lease_epoch)
        finally:
            db.close()
        try:
            context.assert_lease()
            response=provider.execute(effect["request"],
                                      idempotency_key=effect["idempotency_key"])
            if not isinstance(response,dict):
                raise TypeError("Provider execute() must return an object")
            db=self.connection_factory()
            try:
                DurableTaskEffectStore(db.conn).mark_committed(
                    tenant_id=context.tenant_id,task_id=context.task["task_id"],
                    effect_key=effect_key,worker_id=context.worker_id,
                    lease_epoch=context.lease_epoch,
                    provider_reference=response.get("provider_reference"),
                    provider_result=response.get("result",response))
            finally:
                db.close()
            return {"status":"committed","provider":response}
        except ProviderRejected as exc:
            db=self.connection_factory()
            try:
                DurableTaskEffectStore(db.conn).mark_failed(
                    tenant_id=context.tenant_id,task_id=context.task["task_id"],
                    effect_key=effect_key,worker_id=context.worker_id,
                    lease_epoch=context.lease_epoch,
                    error={"type":type(exc).__name__,"message":str(exc)[:2000]})
            finally:
                db.close()
            return {"status":"failed","error":str(exc)}
        except Exception as exc:
            # Any non-definitive exception is ambiguous. Never automatically retry.
            db=self.connection_factory()
            try:
                DurableTaskEffectStore(db.conn).mark_unknown(
                    tenant_id=context.tenant_id,task_id=context.task["task_id"],
                    effect_key=effect_key,worker_id=context.worker_id,
                    lease_epoch=context.lease_epoch,
                    error={"type":type(exc).__name__,"message":str(exc)[:2000]})
            finally:
                db.close()
            return {"status":"unknown","error":str(exc)}

    def reconcile(self, *, context, effect_key: str, provider) -> dict:
        context.assert_lease()
        db=self.connection_factory()
        try:
            effect=DurableTaskEffectStore(db.conn).begin_reconciliation(
                tenant_id=context.tenant_id,task_id=context.task["task_id"],
                effect_key=effect_key,worker_id=context.worker_id,
                lease_epoch=context.lease_epoch)
        finally:
            db.close()
        try:
            outcome=provider.reconcile(
                effect["request"],idempotency_key=effect["idempotency_key"],
                provider_reference=effect["provider_reference"])
            if not isinstance(outcome,dict) or outcome.get("outcome") not in {
                "committed","not_found","unknown","failed"
            }:
                raise ValueError("Provider reconcile() returned an invalid outcome")
        except Exception as exc:
            outcome={"outcome":"unknown","error":{
                "type":type(exc).__name__,"message":str(exc)[:2000]}}
        db=self.connection_factory()
        try:
            DurableTaskEffectStore(db.conn).resolve_reconciliation(
                tenant_id=context.tenant_id,task_id=context.task["task_id"],
                effect_key=effect_key,worker_id=context.worker_id,
                lease_epoch=context.lease_epoch,outcome=outcome["outcome"],
                provider_reference=outcome.get("provider_reference"),
                provider_result=outcome.get("result"),error=outcome.get("error"))
        finally:
            db.close()
        return outcome
