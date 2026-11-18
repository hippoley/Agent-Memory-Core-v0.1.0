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
