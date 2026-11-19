"""Portable evidence envelopes and deterministic recovery adjudication.

This module is deliberately pure: it validates and interprets supplied evidence.
It never reads a database, calls Temporal, contacts a provider, or performs recovery.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, Mapping


SCHEMA_VERSION = "amc.durable-evidence.v1"
EVIDENCE_SOURCES = frozenset({
    "postgres_task",
    "postgres_dispatch",
    "temporal_history",
    "temporal_lookup",
    "activity_heartbeat",
    "effect_ledger",
    "provider_receipt",
})
UNRESOLVED_EFFECT_STATES = frozenset({"executing", "unknown", "reconciling"})
TERMINAL_TASK_STATES = frozenset({"completed", "failed", "cancelled"})
TEMPORAL_TERMINAL_STATES = frozenset({
    "COMPLETED", "FAILED", "CANCELED", "CANCELLED", "TERMINATED", "TIMED_OUT",
})


class EvidenceValidationError(ValueError):
    """An evidence envelope is malformed or its integrity digest is invalid."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EvidenceValidationError("evidence payload must be finite JSON data") from exc


def _parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise EvidenceValidationError("observed_at must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceValidationError("observed_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceValidationError("observed_at must include a timezone")
    return parsed


@dataclass(frozen=True)
class EvidenceEnvelope:
    schema_version: str
    evidence_id: str
    tenant_id: str
    task_id: str
    source: str
    observed_at: str
    payload: Mapping[str, Any]
    workflow_id: str | None = None
    run_id: str | None = None
    lease_epoch: int | None = None
    sha256: str = ""

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_id": self.evidence_id,
            "tenant_id": self.tenant_id,
            "task_id": self.task_id,
            "source": self.source,
            "observed_at": self.observed_at,
            "payload": dict(self.payload),
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "lease_epoch": self.lease_epoch,
        }

    def verify(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise EvidenceValidationError("unsupported evidence schema version")
        if not self.evidence_id or not self.tenant_id or not self.task_id:
            raise EvidenceValidationError("evidence_id, tenant_id and task_id are required")
        if self.source not in EVIDENCE_SOURCES:
            raise EvidenceValidationError(f"unsupported evidence source: {self.source!r}")
        if not isinstance(self.payload, Mapping):
            raise EvidenceValidationError("payload must be a JSON object")
        _parse_timestamp(self.observed_at)
        if self.lease_epoch is not None and (
            isinstance(self.lease_epoch, bool)
            or not isinstance(self.lease_epoch, int)
            or self.lease_epoch < 0
        ):
            raise EvidenceValidationError("lease_epoch must be a non-negative integer")
        expected = hashlib.sha256(_canonical_json(self.unsigned_dict()).encode("utf-8")).hexdigest()
        if self.sha256 != expected:
            raise EvidenceValidationError("evidence SHA-256 mismatch")

    def to_dict(self) -> dict[str, Any]:
        self.verify()
        return {**self.unsigned_dict(), "sha256": self.sha256}

    @classmethod
    def create(
        cls, *, evidence_id: str, tenant_id: str, task_id: str, source: str,
        observed_at: str, payload: Mapping[str, Any],
        workflow_id: str | None = None, run_id: str | None = None,
        lease_epoch: int | None = None,
    ) -> "EvidenceEnvelope":
        item = cls(
            schema_version=SCHEMA_VERSION, evidence_id=evidence_id,
            tenant_id=tenant_id, task_id=task_id, source=source,
            observed_at=observed_at, payload=dict(payload),
            workflow_id=workflow_id, run_id=run_id, lease_epoch=lease_epoch,
        )
        digest = hashlib.sha256(
            _canonical_json(item.unsigned_dict()).encode("utf-8")
        ).hexdigest()
        item = cls(**{**asdict(item), "sha256": digest})
        item.verify()
        return item

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceEnvelope":
        try:
            item = cls(**dict(value))
        except (TypeError, ValueError) as exc:
            raise EvidenceValidationError("invalid evidence envelope fields") from exc
        item.verify()
        return item


@dataclass(frozen=True)
class Adjudication:
    tenant_id: str
    task_id: str
    outcome: str
    execution_status: str
    business_status: str
    effects_status: str
    recovery_authorization: str
    authorized_action: str | None
    evidence_digests: tuple[str, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _latest(items: list[EvidenceEnvelope], source: str) -> EvidenceEnvelope | None:
    candidates = [item for item in items if item.source == source]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (_parse_timestamp(item.observed_at), item.evidence_id))


def adjudicate_evidence(
    evidence: list[EvidenceEnvelope] | tuple[EvidenceEnvelope, ...],
) -> Adjudication:
    """Return a deterministic, side-effect-free adjudication for one task.

    Evidence is compared by domain. PostgreSQL owns business state and leases;
    Temporal history owns orchestration facts; verified provider receipts own
    external-effect outcomes. No source is allowed to overwrite another domain.
    """
    items = list(evidence)
    if not items:
        return Adjudication("", "", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN",
                            "DENY", None, (), ("No evidence was supplied.",))

    try:
        for item in items:
            item.verify()
    except EvidenceValidationError as exc:
        first = items[0]
        return Adjudication(first.tenant_id, first.task_id, "CONFLICT", "UNKNOWN",
                            "UNKNOWN", "UNKNOWN", "DENY", None, (),
                            (f"Evidence validation failed: {exc}",))

    tenant_ids = {item.tenant_id for item in items}
    task_ids = {item.task_id for item in items}
    workflow_ids = {item.workflow_id for item in items if item.workflow_id}
    if len(tenant_ids) != 1 or len(task_ids) != 1 or len(workflow_ids) > 1:
        first = items[0]
        return Adjudication(first.tenant_id, first.task_id, "CONFLICT", "UNKNOWN",
                            "UNKNOWN", "UNKNOWN", "DENY", None,
                            tuple(sorted(item.sha256 for item in items)),
                            ("Evidence identity differs across tenant, task, or Workflow ID.",))

    tenant_id, task_id = next(iter(tenant_ids)), next(iter(task_ids))
    digests = tuple(sorted(item.sha256 for item in items))
    reasons: list[str] = []

    dispatch = _latest(items, "postgres_dispatch")
    task_row = _latest(items, "postgres_task")
    temporal = _latest(items, "temporal_history")
    lookup = _latest(items, "temporal_lookup")
    lease = _latest(items, "activity_heartbeat")
    dispatch_epoch = dispatch.lease_epoch if dispatch else None

    if lookup and str(lookup.payload.get("lookup_status", "")).upper() == "NOT_FOUND" and temporal:
        reasons.append("Temporal lookup reports NOT_FOUND while Temporal history evidence exists.")
    if task_row and "effect_count" in task_row.payload:
        expected_effect_count = task_row.payload.get("effect_count")
        if (
            isinstance(expected_effect_count, bool)
            or not isinstance(expected_effect_count, int)
            or expected_effect_count < 0
        ):
            reasons.append("PostgreSQL effect_count evidence is malformed.")
        elif expected_effect_count != len({
            str(item.payload.get("effect_key", ""))
            for item in effect_items if item.payload.get("effect_key")
        }):
            reasons.append("PostgreSQL effect_count does not match the supplied Effect Ledger evidence.")

    temporal_run_ids = {item.run_id for item in items if item.source == "temporal_history" and item.run_id}
    dispatch_run_ids = {item.run_id for item in items if item.source == "postgres_dispatch" and item.run_id}
    if len(temporal_run_ids) > 1 or len(dispatch_run_ids) > 1:
        reasons.append("Conflicting Run IDs were observed within the same evidence domain.")
    elif temporal_run_ids and dispatch_run_ids and temporal_run_ids != dispatch_run_ids:
        reasons.append("PostgreSQL dispatch Run ID differs from Temporal execution Run ID.")

    task_status = str(task_row.payload.get("status", "UNKNOWN")).lower() if task_row else "UNKNOWN"
    temporal_status = str(temporal.payload.get("execution_status", "UNKNOWN")).upper() if temporal else "UNKNOWN"
    dispatch_status = str(dispatch.payload.get("status", "UNKNOWN")).lower() if dispatch else "UNKNOWN"

    effect_items = [item for item in items if item.source == "effect_ledger"]
    receipt_items = [item for item in items if item.source == "provider_receipt"]
    effect_statuses: dict[str, str] = {}
    effect_keys = {str(item.payload.get("effect_key", "")) for item in effect_items}
    effect_keys.discard("")
    receipt_keys = {str(item.payload.get("effect_key", "")) for item in receipt_items}
    receipt_keys.discard("")
    if receipt_keys - effect_keys:
        reasons.append("Provider receipt exists without a matching PostgreSQL effect intent.")
    for key in effect_keys:
        candidates = [item for item in effect_items if item.payload.get("effect_key") == key]
        latest_effect = max(candidates, key=lambda item: (_parse_timestamp(item.observed_at), item.evidence_id))
        effect_statuses[key] = str(latest_effect.payload.get("status", "UNKNOWN")).lower()

    for key in effect_keys:
        receipts = [item for item in receipt_items if item.payload.get("effect_key") == key]
        if not receipts:
            continue
        latest_receipt = max(receipts, key=lambda item: (_parse_timestamp(item.observed_at), item.evidence_id))
        payload = latest_receipt.payload
        if payload.get("authoritative") is not True or not payload.get("idempotency_key"):
            effect_statuses[key] = "unknown"
            continue
        expected_key = next(
            (str(item.payload["idempotency_key"]) for item in effect_items
             if item.payload.get("effect_key") == key and item.payload.get("idempotency_key")),
            None,
        )
        if expected_key is not None and payload.get("idempotency_key") != expected_key:
            reasons.append(f"Provider receipt idempotency key conflicts for effect {key!r}.")
            effect_statuses[key] = "unknown"
            continue
        outcome = str(payload.get("outcome", "UNKNOWN")).upper()
        provider_status = {
            "COMMITTED": "committed",
            "NOT_FOUND": "not_found",
            "REJECTED": "failed",
            "UNKNOWN": "unknown",
        }.get(outcome, "unknown")
        ledger_status = effect_statuses.get(key, "unknown")
        if ledger_status in {"committed", "failed"} and provider_status in {"committed", "failed", "not_found"} and ledger_status != provider_status:
            reasons.append(f"Provider receipt conflicts with terminal Effect Ledger state for effect {key!r}.")
            effect_statuses[key] = "unknown"
        else:
            effect_statuses[key] = provider_status

    unresolved = sorted(
        key for key, status in effect_statuses.items()
        if status in UNRESOLVED_EFFECT_STATES or status not in {
            "requested", "not_found", "committed", "failed"
        }
    )
    if unresolved:
        effects_status = "UNRESOLVED"
    elif any(status == "failed" for status in effect_statuses.values()):
        effects_status = "KNOWN_FAILURE"
    elif effect_statuses and all(status == "committed" for status in effect_statuses.values()):
        effects_status = "ALL_COMMITTED"
    elif effect_statuses:
        effects_status = "NOT_STARTED_OR_NOT_FOUND"
    else:
        effects_status = "NO_EFFECT_EVIDENCE"

    if reasons:
        outcome = "CONFLICT"
        reasons.append("Conflicting execution identity or Run ID evidence must be investigated.")
    elif temporal_status in {"RUNNING", "CONTINUED_AS_NEW"}:
        outcome = "IN_PROGRESS"
        reasons.append("Temporal history confirms an active Workflow execution.")
    elif lease and lease.payload.get("fresh") is True:
        outcome = "IN_PROGRESS"
        reasons.append("A fresh Activity heartbeat confirms forward execution activity.")
    elif any(status in {"executing", "reconciling"} for status in effect_statuses.values()):
        outcome = "IN_PROGRESS"
        reasons.append("At least one external effect is currently executing or reconciling.")
    elif (
        temporal_status == "COMPLETED"
        and task_status == "completed"
        and not unresolved
        and (
            effects_status == "ALL_COMMITTED"
            or (
                effects_status == "NO_EFFECT_EVIDENCE"
                and task_row is not None
                and task_row.payload.get("effect_count") == 0
            )
        )
        and not reasons
    ):
        outcome = "CONFIRMED"
        reasons.append("Temporal completion and PostgreSQL business completion agree; no unresolved effects were observed.")
    elif (
        lookup and str(lookup.payload.get("lookup_status", "")).upper() == "NOT_FOUND"
        and dispatch_status == "dispatching"
        and dispatch is not None
        and dispatch.payload.get("lease_valid") is True
        and bool(dispatch.payload.get("lease_owner"))
        and dispatch_epoch is not None
        and task_row is not None
        and task_row.payload.get("effect_count") == len(effect_keys)
        and not unresolved
        and not reasons
        and all(status in {"requested", "not_found"} for status in effect_statuses.values())
        and task_status not in TERMINAL_TASK_STATES
    ):
        outcome = "NOT_STARTED"
        reasons.append("Temporal explicitly reports NOT_FOUND; the current dispatch lease is valid and all observed effects are safe to start.")
    else:
        outcome = "UNKNOWN"
        reasons.append("Available evidence does not establish a safe, consistent execution outcome.")

    if outcome == "NOT_STARTED":
        authorization, action = "ALLOW", "START_OR_RECOVER_TEMPORAL"
        reasons.append("Authorization is conditional on rechecking the same current lease epoch at the mutation boundary.")
    elif outcome in {"CONFLICT", "UNKNOWN", "IN_PROGRESS"} or effects_status == "UNRESOLVED":
        authorization, action = "DENY", None
    else:
        authorization, action = "DENY", None

    return Adjudication(
        tenant_id=tenant_id, task_id=task_id, outcome=outcome,
        execution_status=temporal_status, business_status=task_status.upper(),
        effects_status=effects_status, recovery_authorization=authorization,
        authorized_action=action, evidence_digests=digests,
        reasons=tuple(reasons),
    )
