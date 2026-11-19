"""Tests for producer identity, four-dimensional authority and semantic facts."""
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_memory_core.durable_evidence import EvidenceEnvelope
from agent_memory_core.evidence_trust import (
    EvidenceFactConflictError,
    EvidenceReplayError,
    EvidenceTrustError,
    export_public_key,
    sign_evidence,
    verify_and_consume_evidence,
)

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
ISSUER = "https://collector.example.test"
KEY_ID = "collector-ed25519-01"
AUDIENCE = "amc-evidence-ingest"
TENANT = "tenant-a"
TASK_TYPE = "example.task"
CLAIM = "workflow.execution_status"


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, *, public_key, grants=None, audience=AUDIENCE,
                 revoked_at=None, not_before=None, key_expires=None,
                 principal_status="active", principal_type="service", tenant_status="active"):
        self.key = (
            public_key, audience, not_before or NOW-timedelta(days=1),
            key_expires, revoked_at, principal_status, principal_type, tenant_status,
        )
        self.grants = grants if grants is not None else [{
            "source": "temporal_history", "claim_type": CLAIM,
            "tenant_scope": TENANT, "task_type_scope": TASK_TYPE,
            "allow_authoritative": False,
        }]
        self.nonces = set()
        self.facts = {}

    def transaction(self):
        return nullcontext()

    def execute(self, query, params):
        if "FROM memory_evidence_producer_keys" in query:
            return FakeResult(self.key)
        if "FROM memory_evidence_authority_grants" in query:
            issuer, key_id, source, claim_type, tenant, task_type = params[:6]
            matches = [
                g for g in self.grants
                if g["source"] == source and g["claim_type"] == claim_type
                and g["tenant_scope"] in (tenant, "*")
                and g["task_type_scope"] in (task_type, "*")
            ]
            matches.sort(key=lambda g: (g["tenant_scope"] == tenant, g["task_type_scope"] == task_type), reverse=True)
            return FakeResult((matches[0]["allow_authoritative"],) if matches else None)
        if "INSERT INTO memory_evidence_nonces" in query:
            nonce_key = (params[0], params[1], params[2])
            if nonce_key in self.nonces:
                return FakeResult(None)
            self.nonces.add(nonce_key)
            return FakeResult((params[2],))
        if "INSERT INTO memory_evidence_facts" in query:
            fact_key = tuple(params[:8])
            digest = params[8]
            if fact_key in self.facts:
                return FakeResult(None)
            self.facts[fact_key] = digest
            return FakeResult((digest,))
        if "SELECT semantic_sha256 FROM memory_evidence_facts" in query:
            fact_key = tuple(params)
            digest = self.facts.get(fact_key)
            return FakeResult((digest,) if digest else None)
        raise AssertionError(f"Unexpected SQL: {query}")


def sample_evidence(*, source="temporal_history", payload=None, evidence_id="event-envelope-1",
                    observed_at=None, task_id="task-001", tenant_id=TENANT,
                    workflow_id="workflow-001", run_id="run-001", lease_epoch=4):
    body = {"task_type": TASK_TYPE, "event_id": "temporal-event-0001",
            "execution_status": "COMPLETED"}
    body.update(payload or {})
    if source in {"effect_ledger", "provider_receipt"}:
        body.setdefault("effect_key", "device-write")
    return EvidenceEnvelope.create(
        evidence_id=evidence_id, tenant_id=tenant_id, task_id=task_id,
        source=source, observed_at=(observed_at or NOW.isoformat()),
        payload=body, workflow_id=workflow_id, run_id=run_id,
        lease_epoch=lease_epoch,
    )


def sample_attestation(evidence, private_key, *, nonce="nonce-0123456789abcdef",
                       audience=AUDIENCE, claim_type=CLAIM, issued_at=None, expires_at=None):
    return sign_evidence(
        evidence, issuer=ISSUER, key_id=KEY_ID, audience=audience,
        issued_at=(issued_at or NOW).isoformat(),
        expires_at=(expires_at or NOW+timedelta(minutes=2)).isoformat(),
        nonce=nonce, claim_type=claim_type, private_key=private_key,
    )


def verifier(private_key, **kwargs):
    return FakeConnection(public_key=export_public_key(private_key), **kwargs)


def test_valid_ed25519_attestation_is_verified_and_nonce_consumed():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key)
    conn = verifier(private_key)
    result = verify_and_consume_evidence(conn, evidence, proof, expected_audience=AUDIENCE, now=NOW)
    assert result.evidence == evidence
    assert result.issuer == ISSUER and result.key_id == KEY_ID
    assert result.fact_disposition == "NEW"
    assert len(conn.nonces) == 1


def test_same_attestation_nonce_cannot_be_replayed():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key)
    conn = verifier(private_key)
    verify_and_consume_evidence(conn, evidence, proof, expected_audience=AUDIENCE, now=NOW)
    with pytest.raises(EvidenceReplayError, match="already been consumed"):
        verify_and_consume_evidence(conn, evidence, proof, expected_audience=AUDIENCE, now=NOW)


def test_invalid_signature_is_rejected_before_nonce_consumption():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = replace(sample_attestation(evidence, private_key), signature="A"*86)
    conn = verifier(private_key)
    with pytest.raises(EvidenceTrustError, match="signature verification failed"):
        verify_and_consume_evidence(conn, evidence, proof, expected_audience=AUDIENCE, now=NOW)
    assert not conn.nonces


def test_attestation_cannot_be_transplanted_to_another_evidence_envelope():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key)
    other = sample_evidence(evidence_id="different-event", task_id="task-002", workflow_id="workflow-002", run_id="run-002")
    conn = verifier(private_key)
    with pytest.raises(EvidenceTrustError, match="do not bind"):
        verify_and_consume_evidence(conn, other, proof, expected_audience=AUDIENCE, now=NOW)
    assert not conn.nonces


def test_wrong_audience_is_rejected():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key, audience="other-service")
    with pytest.raises(EvidenceTrustError, match="audience mismatch"):
        verify_and_consume_evidence(verifier(private_key), evidence, proof, expected_audience=AUDIENCE, now=NOW)


def test_source_claim_tenant_and_task_type_authority_are_all_enforced():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key)
    conn = verifier(private_key, grants=[{
        "source": "temporal_history", "claim_type": CLAIM,
        "tenant_scope": "tenant-b", "task_type_scope": "other.task",
        "allow_authoritative": False,
    }])
    with pytest.raises(EvidenceTrustError, match="no authority grant"):
        verify_and_consume_evidence(conn, evidence, proof, expected_audience=AUDIENCE, now=NOW)


def test_worker_key_cannot_assert_provider_receipt_claim_without_grant():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence(source="provider_receipt")
    proof = sample_attestation(evidence, private_key, claim_type="provider.effect_committed")
    conn = verifier(private_key)
    with pytest.raises(EvidenceTrustError, match="no authority grant"):
        verify_and_consume_evidence(conn, evidence, proof, expected_audience=AUDIENCE, now=NOW)


def test_provider_key_cannot_attest_another_tenant():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence(source="provider_receipt", tenant_id="tenant-b")
    proof = sample_attestation(evidence, private_key, claim_type="provider.effect_committed")
    conn = verifier(private_key, grants=[{
        "source": "provider_receipt", "claim_type": "provider.effect_committed",
        "tenant_scope": TENANT, "task_type_scope": "*", "allow_authoritative": True,
    }])
    with pytest.raises(EvidenceTrustError, match="no authority grant"):
        verify_and_consume_evidence(conn, evidence, proof, expected_audience=AUDIENCE, now=NOW)


def test_authoritative_payload_flag_requires_explicit_authority_grant():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence(payload={"authoritative": True})
    proof = sample_attestation(evidence, private_key)
    conn = verifier(private_key)
    with pytest.raises(EvidenceTrustError, match="not authorized to assert authoritative"):
        verify_and_consume_evidence(conn, evidence, proof, expected_audience=AUDIENCE, now=NOW)


def test_authoritative_claim_is_accepted_only_with_explicit_grant():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence(payload={"authoritative": True})
    proof = sample_attestation(evidence, private_key)
    conn = verifier(private_key, grants=[{
        "source": "temporal_history", "claim_type": CLAIM,
        "tenant_scope": TENANT, "task_type_scope": TASK_TYPE,
        "allow_authoritative": True,
    }])
    result = verify_and_consume_evidence(
        conn, evidence, proof, expected_audience=AUDIENCE, now=NOW,
    )
    assert result.fact_disposition == "NEW"


def test_revoked_producer_key_is_rejected():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key)
    conn = verifier(private_key, revoked_at=NOW-timedelta(seconds=1))
    with pytest.raises(EvidenceTrustError, match="revoked"):
        verify_and_consume_evidence(conn, evidence, proof, expected_audience=AUDIENCE, now=NOW)


def test_inactive_or_non_service_principal_cannot_attest_evidence():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key)
    with pytest.raises(EvidenceTrustError, match="service principal is not active"):
        verify_and_consume_evidence(verifier(private_key, principal_status="suspended"), evidence, proof, expected_audience=AUDIENCE, now=NOW)
    with pytest.raises(EvidenceTrustError, match="service principal is not active"):
        verify_and_consume_evidence(verifier(private_key, principal_type="human"), evidence, proof, expected_audience=AUDIENCE, now=NOW)


def test_inactive_tenant_cannot_accept_evidence():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key)
    with pytest.raises(EvidenceTrustError, match="tenant is not active"):
        verify_and_consume_evidence(verifier(private_key, tenant_status="suspended"), evidence, proof, expected_audience=AUDIENCE, now=NOW)


def test_expired_and_future_attestations_are_rejected():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    expired = sample_attestation(evidence, private_key, issued_at=NOW-timedelta(minutes=4), expires_at=NOW-timedelta(minutes=3))
    with pytest.raises(EvidenceTrustError, match="expired"):
        verify_and_consume_evidence(verifier(private_key), evidence, expired, expected_audience=AUDIENCE, now=NOW)
    future = sample_attestation(evidence, private_key, nonce="nonce-0123456789abcdeg", issued_at=NOW+timedelta(minutes=5), expires_at=NOW+timedelta(minutes=6))
    with pytest.raises(EvidenceTrustError, match="future"):
        verify_and_consume_evidence(verifier(private_key), evidence, future, expected_audience=AUDIENCE, now=NOW)


def test_stale_observation_is_rejected_even_with_fresh_attestation():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence(observed_at=(NOW-timedelta(hours=1)).isoformat())
    proof = sample_attestation(evidence, private_key)
    with pytest.raises(EvidenceTrustError, match="observation is stale"):
        verify_and_consume_evidence(verifier(private_key), evidence, proof, expected_audience=AUDIENCE, now=NOW)


def test_same_fact_with_new_nonce_is_duplicate_not_replay():
    private_key = Ed25519PrivateKey.generate()
    first = sample_evidence(evidence_id="envelope-a")
    second = sample_evidence(evidence_id="envelope-b", observed_at=(NOW+timedelta(seconds=1)).isoformat())
    conn = verifier(private_key)
    p1 = sample_attestation(first, private_key, nonce="nonce-0123456789abcdef")
    p2 = sample_attestation(second, private_key, nonce="nonce-0123456789abcdeg")
    a = verify_and_consume_evidence(conn, first, p1, expected_audience=AUDIENCE, now=NOW)
    b = verify_and_consume_evidence(conn, second, p2, expected_audience=AUDIENCE, now=NOW+timedelta(seconds=1))
    assert a.fact_disposition == "NEW"
    assert b.fact_disposition == "DUPLICATE"
    assert len(conn.nonces) == 2


def test_same_fact_identity_with_different_payload_is_semantic_conflict():
    private_key = Ed25519PrivateKey.generate()
    first = sample_evidence(evidence_id="envelope-a")
    second = sample_evidence(evidence_id="envelope-b", payload={"execution_status": "FAILED"})
    conn = verifier(private_key)
    p1 = sample_attestation(first, private_key, nonce="nonce-0123456789abcdef")
    p2 = sample_attestation(second, private_key, nonce="nonce-0123456789abcdeg")
    verify_and_consume_evidence(conn, first, p1, expected_audience=AUDIENCE, now=NOW)
    with pytest.raises(EvidenceFactConflictError, match="conflicting semantic content"):
        verify_and_consume_evidence(conn, second, p2, expected_audience=AUDIENCE, now=NOW)


def test_trusted_adjudication_only_receives_verified_evidence():
    from agent_memory_core.evidence_trust import adjudicate_trusted_evidence
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key)
    result = adjudicate_trusted_evidence(
        verifier(private_key), [(evidence, proof)],
        expected_audience=AUDIENCE, now=NOW,
    )
    assert result.adjudication.outcome == "UNKNOWN"
    assert result.adjudication.recovery_authorization == "DENY"
    assert result.verified_evidence[0].issuer == ISSUER
