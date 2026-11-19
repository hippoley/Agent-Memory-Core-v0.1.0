"""Read-only PostgreSQL ↔ Temporal lifecycle comparison.

This module deliberately classifies divergence without repairing either system.
PostgreSQL remains the business-state authority; Temporal is orchestration history.
Any proposed recovery must be an explicit, separately audited operation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


UNRESOLVED_EFFECT_STATES=frozenset({"requested","executing","unknown","reconciling"})
PG_TERMINAL=frozenset({"completed","failed","cancelled"})
TEMPORAL_TERMINAL=frozenset({
    "COMPLETED","FAILED","CANCELED","CANCELLED","TERMINATED","TIMED_OUT"
})


@dataclass(frozen=True)
class TemporalTaskAssessment:
    classification: str
    safe_for_automatic_repair: bool
    task_id: str
    workflow_id: str | None
    postgres_status: str | None
    temporal_status: str | None
    unresolved_effect_keys: tuple[str,...]
    reason: str


def assess_temporal_task(*, task: dict | None, temporal_status: str | None,
                         effects: list[dict] | None = None,
                         temporal_exists: bool = True) -> TemporalTaskAssessment:
    """Classify a single task/workflow pair; never mutate either system."""
    effects=effects or []
    unresolved=tuple(sorted(
        str(e.get("effect_key")) for e in effects
        if str(e.get("status","")).lower() in UNRESOLVED_EFFECT_STATES
    ))
    if task is None:
        return TemporalTaskAssessment(
            "ORPHAN_TEMPORAL" if temporal_exists else "POSTGRES_TASK_MISSING",
            False,"",None,None,temporal_status,unresolved,
            "No PostgreSQL task row exists for this Temporal execution; do not recreate business state automatically.")
    task_id=str(task.get("task_id",""))
    workflow_id=task.get("workflow_id")
    pg=(task.get("status") or "").lower()
    tw=(temporal_status or "").upper() if temporal_exists else None

    if pg=="completed" and unresolved:
        return TemporalTaskAssessment(
            "PG_COMPLETED_WITH_UNRESOLVED_EFFECTS",False,task_id,workflow_id,pg,tw,
            unresolved,"A completed task still has unresolved external effects; preserve evidence and block further completion claims.")
    if not temporal_exists:
        return TemporalTaskAssessment(
            "TEMPORAL_EXECUTION_MISSING",False,task_id,workflow_id,pg,None,unresolved,
            "PostgreSQL references a task without a discoverable Temporal execution; deterministic re-start requires a separate idempotent dispatch protocol.")
    if tw=="COMPLETED" and pg not in PG_TERMINAL:
        return TemporalTaskAssessment(
            "TEMPORAL_COMPLETED_POSTGRES_NONTERMINAL",False,task_id,workflow_id,pg,tw,
            unresolved,"Temporal completion is not business completion; inspect activity receipts and effects before any PostgreSQL transition.")
    if tw in {"CANCELED","CANCELLED","TERMINATED","TIMED_OUT"} and pg not in PG_TERMINAL:
        return TemporalTaskAssessment(
            "TEMPORAL_STOPPED_POSTGRES_NONTERMINAL",False,task_id,workflow_id,pg,tw,
            unresolved,"Temporal stopped, but PostgreSQL is nonterminal; cancellation does not prove remote effects were stopped or rolled back.")
    if tw=="FAILED" and pg not in PG_TERMINAL:
        return TemporalTaskAssessment(
            "TEMPORAL_FAILED_POSTGRES_NONTERMINAL",False,task_id,workflow_id,pg,tw,
            unresolved,"Temporal failure does not prove business failure; inspect durable task and provider evidence.")
    if pg=="completed" and tw!="COMPLETED":
        return TemporalTaskAssessment(
            "POSTGRES_COMPLETED_TEMPORAL_NOT_COMPLETED",False,task_id,workflow_id,pg,tw,
            unresolved,"PostgreSQL is authoritative for business state; Temporal history is divergent and must be retained for audit.")
    if pg in {"failed","cancelled"} and tw=="COMPLETED":
        return TemporalTaskAssessment(
            "TERMINAL_STATE_DIVERGENCE",False,task_id,workflow_id,pg,tw,
            unresolved,"Terminal business state conflicts with Temporal completion; no automatic overwrite is safe.")
    if pg in PG_TERMINAL and tw in TEMPORAL_TERMINAL:
        return TemporalTaskAssessment(
            "ALIGNED_TERMINAL",False,task_id,workflow_id,pg,tw,unresolved,
            "Both systems are terminal; retain both records and verify external-effect receipts.")
    if pg not in PG_TERMINAL and tw not in TEMPORAL_TERMINAL:
        return TemporalTaskAssessment(
            "ALIGNED_NONTERMINAL",False,task_id,workflow_id,pg,tw,unresolved,
            "Both systems are nonterminal; continue observing without mutating business state.")
    return TemporalTaskAssessment(
        "UNCLASSIFIED_DIVERGENCE",False,task_id,workflow_id,pg,tw,unresolved,
        "State combination is not covered by the current recovery policy; require manual review.")

async def inspect_temporal_dispatches(
    conn: Any, client: Any, *, tenant_id: str, limit: int = 100
) -> list[dict]:
    """Read-only live reconciliation of PostgreSQL dispatch rows and Temporal.

    The tenant predicate is mandatory. No PostgreSQL or Temporal state is
    mutated. Lookup failures are reported separately from missing executions;
    callers must never interpret a transport error as proof that a Workflow
    does not exist.
    """
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError("tenant_id is required")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
        raise ValueError("limit must be an integer between 1 and 500")

    rows = conn.execute(
        """SELECT
               t.task_id::text, t.tenant_id, t.task_type, t.workflow_id,
               t.status AS task_status,
               d.status AS dispatch_status, d.task_queue, d.temporal_run_id,
               d.lease_owner, d.lease_epoch, d.attempts, d.lease_expires_at,
               COALESCE((
                   SELECT jsonb_agg(jsonb_build_object(
                       'effect_key', e.effect_key, 'status', e.status
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

    report: list[dict] = []
    for row in rows:
        (
            task_id, row_tenant, task_type, workflow_id, task_status,
            dispatch_status, task_queue, recorded_run_id, lease_owner,
            lease_epoch, attempts, lease_expires_at, effects,
        ) = row
        task = {
            "task_id": str(task_id),
            "tenant_id": row_tenant,
            "task_type": task_type,
            "workflow_id": workflow_id,
            "status": task_status,
        }
        try:
            description = await client.get_workflow_handle(workflow_id).describe()
        except Exception as exc:
            if type(exc).__name__ == "WorkflowNotFoundError":
                temporal_exists = False
                temporal_status = None
                temporal_run_id = None
                identity_conflict = None
                lookup_error = None
            else:
                report.append({
                    "task_id": str(task_id),
                    "tenant_id": row_tenant,
                    "workflow_id": workflow_id,
                    "task_status": task_status,
                    "dispatch_status": dispatch_status,
                    "classification": "TEMPORAL_LOOKUP_FAILED",
                    "safe_for_automatic_repair": False,
                    "temporal_status": None,
                    "temporal_run_id": None,
                    "recorded_temporal_run_id": recorded_run_id,
                    "identity_conflict": None,
                    "unresolved_effect_keys": [],
                    "reason": "Temporal lookup failed; execution existence is unknown and must not be inferred from a transport/client error.",
                    "lookup_error": type(exc).__name__,
                })
                continue
        else:
            temporal_exists = True
            status_value = getattr(description, "status", None)
            temporal_status = getattr(status_value, "name", status_value)
            temporal_status = str(temporal_status).upper() if temporal_status is not None else None
            temporal_run_id = getattr(description, "run_id", None)
            workflow_type = getattr(description, "workflow_type", None)
            workflow_type = getattr(workflow_type, "name", workflow_type)
            memo = getattr(description, "memo", None) or {}
            memo_identity = memo.get("agent_memory_task_identity") if isinstance(memo, dict) else None
            expected_identity = {
                "tenant_id": row_tenant,
                "task_id": str(task_id),
                "workflow_id": workflow_id,
                "task_type": task_type,
            }
            identity_conflict = None
            if (
                getattr(description, "id", None) != workflow_id
                or workflow_type != "DurableTaskWorkflow"
                or getattr(description, "task_queue", None) != task_queue
                or memo_identity != expected_identity
            ):
                identity_conflict = {
                    "workflow_id_matches": getattr(description, "id", None) == workflow_id,
                    "workflow_type_matches": workflow_type == "DurableTaskWorkflow",
                    "task_queue_matches": getattr(description, "task_queue", None) == task_queue,
                    "memo_identity_matches": memo_identity == expected_identity,
                }

        effects_list = effects or []
        if isinstance(effects_list, str):
            import json
            effects_list = json.loads(effects_list)
        assessment = assess_temporal_task(
            task=task,
            temporal_status=temporal_status,
            effects=effects_list,
            temporal_exists=temporal_exists,
        )
        classification = (
            "TEMPORAL_IDENTITY_CONFLICT" if identity_conflict else assessment.classification
        )
        reason = (
            "Temporal execution exists under the expected Workflow ID but its type, task queue, or immutable memo identity differs from PostgreSQL. Preserve both records; do not acknowledge or repair automatically."
            if identity_conflict else assessment.reason
        )
        if recorded_run_id and temporal_run_id and recorded_run_id != temporal_run_id:
            classification = "TEMPORAL_RUN_ID_DIVERGENCE"
            reason = "The Temporal Run ID differs from the Run ID recorded in PostgreSQL. Preserve both identifiers and require investigation."
        report.append({
            "task_id": str(task_id),
            "tenant_id": row_tenant,
            "workflow_id": workflow_id,
            "task_type": task_type,
            "task_status": task_status,
            "dispatch_status": dispatch_status,
            "task_queue": task_queue,
            "lease_owner": lease_owner,
            "lease_epoch": lease_epoch,
            "attempts": attempts,
            "lease_expires_at": lease_expires_at.isoformat() if hasattr(lease_expires_at, "isoformat") else lease_expires_at,
            "classification": classification,
            "safe_for_automatic_repair": False,
            "temporal_status": temporal_status,
            "temporal_run_id": temporal_run_id,
            "recorded_temporal_run_id": recorded_run_id,
            "identity_conflict": identity_conflict,
            "unresolved_effect_keys": list(assessment.unresolved_effect_keys),
            "reason": reason,
            "lookup_error": None,
        })
    return report

def main() -> None:
    """Run a tenant-scoped, read-only PostgreSQL/Temporal reconciliation report."""
    import argparse
    import asyncio
    import json
    import os
    import sys

    parser = argparse.ArgumentParser(
        description="Inspect PostgreSQL durable tasks against Temporal without mutating either system."
    )
    parser.add_argument("--tenant-id", required=True, help="Exact tenant scope to inspect")
    parser.add_argument("--limit", type=int, default=100, help="Maximum tasks (1..500)")
    args = parser.parse_args()
    if not 1 <= args.limit <= 500:
        parser.error("--limit must be between 1 and 500")

    async def run() -> list[dict]:
        from temporalio.client import Client
        from .postgres import PostgresMemory
        from .temporal_adapter import _database_dsn

        db = PostgresMemory(_database_dsn())
        try:
            client = await Client.connect(
                os.getenv("MEMORY_TEMPORAL_ADDRESS", "localhost:7233"),
                namespace=os.getenv("MEMORY_TEMPORAL_NAMESPACE", "default"),
            )
            return await inspect_temporal_dispatches(
                db.conn, client, tenant_id=args.tenant_id, limit=args.limit
            )
        finally:
            db.close()

    try:
        report = asyncio.run(run())
    except Exception as exc:
        print(json.dumps({
            "error": type(exc).__name__,
            "message": str(exc),
            "safe_for_automatic_repair": False,
        }, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from exc

    print(json.dumps({
        "mode": "read_only",
        "tenant_id": args.tenant_id,
        "count": len(report),
        "automatic_repair": "disabled",
        "items": report,
    }, ensure_ascii=False, indent=2, default=str))
    if any(item["classification"] not in {"ALIGNED_NONTERMINAL", "ALIGNED_TERMINAL"}
           for item in report):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
