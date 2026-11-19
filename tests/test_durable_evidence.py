"""Conformance tests for portable durable execution evidence adjudication."""
from dataclasses import replace

import pytest

from agent_memory_core.durable_evidence import (
    EvidenceEnvelope,
    EvidenceValidationError,
    adjudicate_evidence,
)


STAMP = "2026-11-19T10:00:00+00:00"
TENANT = "tenant-a"
TASK = "task-a"
WORKFLOW = "workflow-a"
RUN = "run-a"


def ev(source, payload, *, evidence_id=None, workflow_id=WORKFLOW,
       run_id=None, lease_epoch=None, observed_at=STAMP):
    return EvidenceEnvelope.create(
        evidence_id=evidence_id or f"{source}-{len(payload)}-{run_id or 'none'}",
        tenant_id=TENANT, task_id=TASK, source=source,
        observed_at=observed_at, payload=payload,
        workflow_id=workflow_id, run_id=run_id, lease_epoch=lease_epoch,
    )


def test_evidence_envelope_round_trip_and_digest_tampering():
    item = ev("postgres_task", {"status": "running", "effect_count": 0})
    restored = EvidenceEnvelope.from_dict(item.to_dict())
    assert restored == item
    damaged = replace(item, payload={"status": "completed", "effect_count": 0})
    with pytest.raises(EvidenceValidationError, match="SHA-256"):
        damaged.verify()


def test_evidence_requires_timezone_and_known_source():
    with pytest.raises(EvidenceValidationError, match="timezone"):
        ev("postgres_task", {"status": "running"}, observed_at="2026-11-19T10:00:00")
    with pytest.raises(EvidenceValidationError, match="unsupported evidence source"):
        ev("mystery", {})


def test_empty_evidence_is_unknown_and_denied():
    result = adjudicate_evidence([])
    assert result.outcome == "UNKNOWN"
    assert result.recovery_authorization == "DENY"


def test_cross_tenant_or_workflow_identity_is_conflict():
    result = adjudicate_evidence([
        ev("postgres_task", {"status": "running"}, workflow_id="workflow-a"),
        EvidenceEnvelope.create(
            evidence_id="other-tenant", tenant_id="tenant-b", task_id=TASK,
            source="temporal_history", observed_at=STAMP,
            payload={"execution_status": "RUNNING"}, workflow_id="workflow-a",
        ),
    ])
    assert result.outcome == "CONFLICT"
    assert result.recovery_authorization == "DENY"


def test_temporal_run_id_divergence_is_conflict():
    result = adjudicate_evidence([
        ev("postgres_task", {"status": "running", "effect_count": 0}),
        ev("postgres_dispatch", {"status": "started"}, run_id="run-old"),
        ev("temporal_history", {"execution_status": "RUNNING"}, run_id="run-new"),
    ])
    assert result.outcome == "CONFLICT"
    assert any("Run ID" in reason for reason in result.reasons)


def test_running_temporal_history_is_in_progress_not_recoverable():
    result = adjudicate_evidence([
        ev("postgres_task", {"status": "running", "effect_count": 0}),
        ev("temporal_history", {"execution_status": "RUNNING"}, run_id=RUN),
    ])
    assert result.outcome == "IN_PROGRESS"
    assert result.recovery_authorization == "DENY"


def test_fresh_activity_heartbeat_is_in_progress():
    result = adjudicate_evidence([
        ev("postgres_task", {"status": "running", "effect_count": 0}),
        ev("activity_heartbeat", {"fresh": True, "activity_id": "activity-1"}, run_id=RUN),
    ])
    assert result.outcome == "IN_PROGRESS"


def test_unknown_effect_blocks_recovery_even_if_temporal_completed():
    result = adjudicate_evidence([
        ev("postgres_task", {"status": "running", "effect_count": 1}),
        ev("temporal_history", {"execution_status": "COMPLETED"}, run_id=RUN),
        ev("effect_ledger", {"effect_key": "device-write", "status": "unknown",
                             "idempotency_key": "idem-1"}),
    ])
    assert result.outcome == "UNKNOWN"
    assert result.effects_status == "UNRESOLVED"
    assert result.recovery_authorization == "DENY"


def test_authoritative_provider_receipt_can_resolve_unknown_effect():
    result = adjudicate_evidence([
        ev("postgres_task", {"status": "completed", "effect_count": 1}),
        ev("temporal_history", {"execution_status": "COMPLETED"}, run_id=RUN),
        ev("effect_ledger", {"effect_key": "device-write", "status": "unknown",
                             "idempotency_key": "idem-1"}),
        ev("provider_receipt", {"effect_key": "device-write", "outcome": "COMMITTED",
                                "authoritative": True, "idempotency_key": "idem-1"}),
    ])
    assert result.outcome == "CONFIRMED"
    assert result.effects_status == "ALL_COMMITTED"
    assert result.recovery_authorization == "DENY"


def test_unverified_provider_receipt_does_not_resolve_unknown():
    result = adjudicate_evidence([
        ev("postgres_task", {"status": "running", "effect_count": 1}),
        ev("effect_ledger", {"effect_key": "device-write", "status": "unknown",
                             "idempotency_key": "idem-1"}),
        ev("provider_receipt", {"effect_key": "device-write", "outcome": "NOT_FOUND",
                                "authoritative": False, "idempotency_key": "idem-1"}),
    ])
    assert result.outcome == "UNKNOWN"
    assert result.effects_status == "UNRESOLVED"


def test_provider_receipt_conflicting_with_terminal_effect_is_conflict():
    result = adjudicate_evidence([
        ev("postgres_task", {"status": "completed", "effect_count": 1}),
        ev("temporal_history", {"execution_status": "COMPLETED"}, run_id=RUN),
        ev("effect_ledger", {"effect_key": "charge", "status": "committed",
                             "idempotency_key": "charge-key"}),
        ev("provider_receipt", {"effect_key": "charge", "outcome": "NOT_FOUND",
                                "authoritative": True, "idempotency_key": "charge-key"}),
    ])
    assert result.outcome == "CONFLICT"
    assert result.recovery_authorization == "DENY"


def test_verified_not_found_with_current_dispatch_lease_allows_only_conditional_start():
    result = adjudicate_evidence([
        ev("postgres_task", {"status": "running", "effect_count": 0}),
        ev("postgres_dispatch", {"status": "dispatching", "lease_valid": True,
                                 "lease_owner": "dispatcher-a"}, lease_epoch=7),
        ev("temporal_lookup", {"lookup_status": "NOT_FOUND"}),
    ])
    assert result.outcome == "NOT_STARTED"
    assert result.recovery_authorization == "ALLOW"
    assert result.authorized_action == "START_OR_RECOVER_TEMPORAL"
    assert any("rechecking" in reason for reason in result.reasons)


def test_not_found_without_current_lease_is_not_authorized():
    result = adjudicate_evidence([
        ev("postgres_task", {"status": "running", "effect_count": 0}),
        ev("postgres_dispatch", {"status": "dispatching", "lease_valid": False,
                                 "lease_owner": "dispatcher-a"}, lease_epoch=7),
        ev("temporal_lookup", {"lookup_status": "NOT_FOUND"}),
    ])
    assert result.outcome == "UNKNOWN"
    assert result.recovery_authorization == "DENY"


def test_missing_effect_ledger_rows_cannot_be_assumed_empty():
    result = adjudicate_evidence([
        ev("postgres_task", {"status": "completed"}),
        ev("temporal_history", {"execution_status": "COMPLETED"}, run_id=RUN),
    ])
    assert result.outcome == "UNKNOWN"
    assert result.effects_status == "NO_EFFECT_EVIDENCE"


def test_provider_receipt_without_matching_effect_intent_is_conflict():
    result = adjudicate_evidence([
        ev("postgres_task", {"status": "running", "effect_count": 0}),
        ev("provider_receipt", {"effect_key": "orphan", "outcome": "COMMITTED",
                                "authoritative": True, "idempotency_key": "idem"}),
    ])
    assert result.outcome == "CONFLICT"
