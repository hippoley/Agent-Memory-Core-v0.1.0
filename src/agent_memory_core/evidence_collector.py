"""Read-only live evidence collection from PostgreSQL and Temporal.

This collector emits observations, not conclusions. Temporal describe metadata is
labelled temporal_lookup; it is never mislabeled as full Workflow History.
Provider results stored locally are Effect Ledger observations, not provider-
authenticated receipts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .durable_effects import provider_idempotency_key
from .durable_evidence import EvidenceEnvelope


@dataclass(frozen=True)
class EvidenceCollection:
    tenant_id: str
    requested_limit: int
    task_count: int
    dispatch_scope_complete: bool
    captured_at: str
    evidence: tuple[EvidenceEnvelope, ...]
    source_coverage: tuple[str, ...]
    known_gaps: tuple[str, ...]

    def require_complete(self) -> tuple[EvidenceEnvelope, ...]:
        if not self.dispatch_scope_complete:
            raise RuntimeError("Evidence collection was truncated; do not adjudicate it as complete")
        return self.evidence


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return datetime.now(timezone.utc).isoformat()


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _rpc_status_name(exc: Exception) -> str | None:
    status = getattr(exc, "status", None)
    return getattr(status, "name", status)


async def collect_live_temporal_dispatch_evidence(
    conn: Any, client: Any, *, tenant_id: str, limit: int = 100,
) -> EvidenceCollection:
    """Collect a tenant-scoped snapshot without mutating either backing system.

    Sources emitted:
      - postgres_task
      - postgres_dispatch
      - postgres_worker_lease
      - effect_ledger (one envelope per effect)
      - temporal_lookup (describe metadata or an explicit lookup failure)

    This function intentionally does not claim to collect full Temporal History,
    Activity Heartbeats, or independently authenticated Provider Receipts.
    """
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError("tenant_id is required")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
        raise ValueError("limit must be an integer between 1 and 500")

    capture_started = datetime.now(timezone.utc)
    rows = conn.execute(
        """SELECT
               t.task_id::text, t.tenant_id, t.task_type, t.workflow_id AS task_workflow_id,
               d.workflow_id AS dispatch_workflow_id,
               t.status AS task_status, t.lease_owner AS worker_lease_owner,
               t.lease_epoch AS worker_lease_epoch,
               t.lease_expires_at AS worker_lease_expires_at,
               t.heartbeat_at AS worker_heartbeat_at,
               (t.lease_expires_at > clock_timestamp()) AS worker_lease_valid,
               d.status AS dispatch_status, d.task_queue, d.temporal_run_id,
               d.lease_owner AS dispatch_lease_owner,
               d.lease_epoch AS dispatch_lease_epoch,
               d.lease_expires_at AS dispatch_lease_expires_at,
               d.attempts AS dispatch_attempts,
               clock_timestamp() AS postgres_observed_at,
               COALESCE((
                   SELECT jsonb_agg(jsonb_build_object(
                       'effect_key', e.effect_key,
                       'status', e.status,
                       'request_sha256', e.request_sha256,
                       'attempt_count', e.attempt_count,
                       'execution_worker_id', e.execution_worker_id,
                       'execution_lease_epoch', e.execution_lease_epoch,
                       'provider_reference', e.provider_reference,
                       'provider_result_present', (e.provider_result IS NOT NULL)
                   ) ORDER BY e.effect_key)
                   FROM memory_task_effects AS e
                   WHERE e.task_id=t.task_id
               ), '[]'::jsonb) AS effects
           FROM memory_durable_tasks AS t
           JOIN memory_temporal_dispatches AS d
             ON d.task_id=t.task_id AND d.tenant_id=t.tenant_id
           WHERE t.tenant_id=%s
           ORDER BY d.updated_at, t.task_id
           LIMIT %s""",
        (tenant_id, limit),
    ).fetchall()

    evidence: list[EvidenceEnvelope] = []
    for row in rows:
        (
            task_id, row_tenant, task_type, task_workflow_id, workflow_id, task_status,
            worker_owner, worker_epoch, worker_expiry, worker_heartbeat,
            worker_lease_valid, dispatch_status, task_queue, recorded_run_id,
            dispatch_owner, dispatch_epoch, dispatch_expiry, dispatch_attempts,
            postgres_observed_at, effects,
        ) = row
        if not workflow_id:
            # A dispatch row without a deterministic Workflow ID is an invalid
            # identity state; do not invent an ID or query Temporal by guess.
            temporal_lookup = {
                "lookup_status": "IDENTITY_INVALID",
                "reason": "PostgreSQL task has no Workflow ID",
            }
            temporal_run_id = None
            temporal_observed_at = datetime.now(timezone.utc).isoformat()
        else:
            try:
                description = await client.get_workflow_handle(workflow_id).describe()
            except Exception as exc:
                status_name = _rpc_status_name(exc)
                if status_name == "NOT_FOUND" or type(exc).__name__ == "WorkflowNotFoundError":
                    temporal_lookup = {"lookup_status": "NOT_FOUND"}
                else:
                    temporal_lookup = {
                        "lookup_status": "ERROR",
                        "error_type": type(exc).__name__,
                    }
                temporal_run_id = None
                temporal_observed_at = datetime.now(timezone.utc).isoformat()
            else:
                status_value = getattr(description, "status", None)
                status_name = getattr(status_value, "name", status_value)
                workflow_type = getattr(description, "workflow_type", None)
                workflow_type = getattr(workflow_type, "name", workflow_type)
                memo_value = getattr(description, "memo", None)
                memo = await memo_value() if callable(memo_value) else (memo_value or {})
                memo_identity = (
                    memo.get("agent_memory_task_identity")
                    if isinstance(memo, dict) else None
                )
                expected_identity = {
                    "tenant_id": row_tenant,
                    "task_id": str(task_id),
                    "workflow_id": workflow_id,
                    "task_type": task_type,
                }
                temporal_run_id = getattr(description, "run_id", None)
                temporal_lookup = {
                    "lookup_status": "FOUND",
                    "execution_status": str(status_name).upper() if status_name is not None else "UNKNOWN",
                    "workflow_type": workflow_type,
                    "task_queue": getattr(description, "task_queue", None),
                    "workflow_id_matches": getattr(description, "id", None) == workflow_id,
                    "memo_identity_matches": memo_identity == expected_identity,
                    "workflow_type_matches": workflow_type == "DurableTaskWorkflow",
                    "task_queue_matches": getattr(description, "task_queue", None) == task_queue,
                    "recorded_run_id_matches": (
                        recorded_run_id is None or temporal_run_id == recorded_run_id
                    ),
                }
                temporal_observed_at = datetime.now(timezone.utc).isoformat()

        pg_time = _iso(postgres_observed_at)
        evidence.append(EvidenceEnvelope.create(
            evidence_id=uuid4().hex, tenant_id=row_tenant, task_id=str(task_id),
            source="postgres_task", observed_at=pg_time,
            workflow_id=workflow_id,
            payload={
                "status": task_status,
                "task_type": task_type,
                "effect_count": len(_json_value(effects) or []),
                "task_workflow_id": task_workflow_id,
                "dispatch_workflow_id_matches": task_workflow_id == workflow_id,
            },
        ))
        evidence.append(EvidenceEnvelope.create(
            evidence_id=uuid4().hex, tenant_id=row_tenant, task_id=str(task_id),
            source="postgres_dispatch", observed_at=pg_time,
            workflow_id=workflow_id, run_id=recorded_run_id,
            lease_epoch=dispatch_epoch,
            payload={
                "status": dispatch_status,
                "task_queue": task_queue,
                "lease_owner": dispatch_owner,
                "lease_valid": bool(dispatch_expiry and dispatch_expiry > postgres_observed_at),
                "lease_expires_at": _iso(dispatch_expiry) if dispatch_expiry else None,
                "attempts": dispatch_attempts,
            },
        ))
        evidence.append(EvidenceEnvelope.create(
            evidence_id=uuid4().hex, tenant_id=row_tenant, task_id=str(task_id),
            source="postgres_worker_lease", observed_at=pg_time,
            workflow_id=workflow_id, lease_epoch=worker_epoch,
            payload={
                "lease_owner": worker_owner,
                "lease_valid": bool(worker_lease_valid),
                "lease_expires_at": _iso(worker_expiry) if worker_expiry else None,
                "heartbeat_at": _iso(worker_heartbeat) if worker_heartbeat else None,
            },
        ))

        for effect in _json_value(effects) or []:
            effect_key = str(effect["effect_key"])
            evidence.append(EvidenceEnvelope.create(
                evidence_id=uuid4().hex, tenant_id=row_tenant, task_id=str(task_id),
                source="effect_ledger", observed_at=pg_time,
                workflow_id=workflow_id,
                lease_epoch=effect.get("execution_lease_epoch"),
                payload={
                    "effect_key": effect_key,
                    "status": effect["status"],
                    "request_sha256": effect.get("request_sha256"),
                    "attempt_count": effect.get("attempt_count"),
                    "execution_worker_id": effect.get("execution_worker_id"),
                    "idempotency_key": provider_idempotency_key(
                        tenant_id=row_tenant, task_id=str(task_id), effect_key=effect_key,
                    ),
                    "provider_reference": effect.get("provider_reference"),
                    "provider_result_present": bool(effect.get("provider_result_present")),
                    "provider_receipt_available": False,
                },
            ))

        evidence.append(EvidenceEnvelope.create(
            evidence_id=uuid4().hex, tenant_id=row_tenant, task_id=str(task_id),
            source="temporal_lookup", observed_at=temporal_observed_at,
            workflow_id=workflow_id, run_id=temporal_run_id,
            payload=temporal_lookup,
        ))

    return EvidenceCollection(
        tenant_id=tenant_id,
        requested_limit=limit,
        task_count=len(rows),
        dispatch_scope_complete=not truncated,
        captured_at=capture_started.isoformat(),
        evidence=tuple(evidence),
        source_coverage=(
            "postgres_task", "postgres_dispatch", "postgres_worker_lease",
            "effect_ledger", "temporal_lookup",
        ),
        known_gaps=(
            "temporal_history",
            "activity_heartbeat",
            "provider_receipt",
            "cross-system atomic snapshot",
            "tasks without a Temporal dispatch-ledger row",
        ),
    )
