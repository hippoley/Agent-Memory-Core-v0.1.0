"""Tests for producer authentication, source authority and nonce replay defense."""
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_memory_core.durable_evidence import EvidenceEnvelope
from agent_memory_core.evidence_trust import (
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


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, *, public_key, allowed_sources=None, allowed_tenants=None,
                 audience=AUDIENCE, revoked_at=None, not_before=None, key_expires=None):
        self.key = (
            public_key,
            allowed_sources or ["temporal_history", "provider_receipt"],
            allowed_tenants or [TENANT],
            audience,
            not_before or NOW - timedelta(days=1),
            key_expires,
            revoked_at,
        )
        self.nonces = set()

    def transaction(self):
        return nullcontext()

    def execute(self, query, params):
        if "FROM memory_evidence_producer_keys" in query:
            return FakeResult(self.key)
        if "INSERT INTO memory_evidence_nonces" in query:
            nonce_key = (params[0], params[1], params[2])
            if nonce_key in self.nonces:
                return FakeResult(None)
            self.nonces.add(nonce_key)
            return FakeResult((params[2],))
        raise AssertionError(f"Unexpected SQL: {query}")


def sample_evidence():
    return EvidenceEnvelope.create(
        evidence_id="temporal-event-0001",
        tenant_id=TENANT,
        task_id="task-001",
        source="temporal_history",
        observed_at=NOW.isoformat(),
        payload={"execution_status": "COMPLETED"},
        workflow_id="workflow-001",
        run_id="run-001",
        lease_epoch=4,
    )


def sample_attestation(evidence, private_key, *, nonce="nonce-0123456789abcdef",
                       audience=AUDIENCE, issued_at=None, expires_at=None):
    return sign_evidence(
        evidence,
        issuer=ISSUER,
        key_id=KEY_ID,
        audience=audience,
        issued_at=(issued_at or NOW).isoformat(),
        expires_at=(expires_at or NOW + timedelta(minutes=2)).isoformat(),
        nonce=nonce,
        private_key=private_key,
    )


def verifier(private_key, **kwargs):
    return FakeConnection(public_key=export_public_key(private_key), **kwargs)


def test_valid_ed25519_attestation_is_verified_and_nonce_consumed():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key)
    conn = verifier(private_key)

    result = verify_and_consume_evidence(
        conn, evidence, proof, expected_audience=AUDIENCE, now=NOW,
    )

    assert result.evidence == evidence
    assert result.issuer == ISSUER
    assert result.key_id == KEY_ID
    assert result.signature_sha256
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
    proof = sample_attestation(evidence, private_key)
    tampered = replace(proof, signature="A" * 86)
    conn = verifier(private_key)

    with pytest.raises(EvidenceTrustError, match="signature verification failed"):
        verify_and_consume_evidence(conn, evidence, tampered, expected_audience=AUDIENCE, now=NOW)
    assert not conn.nonces


def test_attestation_cannot_be_transplanted_to_another_evidence_envelope():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key)
    other = EvidenceEnvelope.create(
        evidence_id="different-event", tenant_id=TENANT, task_id="task-002",
        source="temporal_history", observed_at=NOW.isoformat(),
        payload={"execution_status": "COMPLETED"},
        workflow_id="workflow-002", run_id="run-002", lease_epoch=4,
    )
    conn = verifier(private_key)

    with pytest.raises(EvidenceTrustError, match="do not bind"):
        verify_and_consume_evidence(conn, other, proof, expected_audience=AUDIENCE, now=NOW)
    assert not conn.nonces


def test_wrong_audience_is_rejected():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key, audience="other-service")
    conn = verifier(private_key)

    with pytest.raises(EvidenceTrustError, match="audience mismatch"):
        verify_and_consume_evidence(conn, evidence, proof, expected_audience=AUDIENCE, now=NOW)


def test_producer_source_authority_is_enforced():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key)
    conn = verifier(private_key, allowed_sources=["provider_receipt"])

    with pytest.raises(EvidenceTrustError, match="not authorized for this evidence source"):
        verify_and_consume_evidence(conn, evidence, proof, expected_audience=AUDIENCE, now=NOW)


def test_producer_tenant_authority_is_enforced():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key)
    conn = verifier(private_key, allowed_tenants=["tenant-b"])

    with pytest.raises(EvidenceTrustError, match="not authorized for this tenant"):
        verify_and_consume_evidence(conn, evidence, proof, expected_audience=AUDIENCE, now=NOW)


def test_revoked_producer_key_is_rejected():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key)
    conn = verifier(private_key, revoked_at=NOW - timedelta(seconds=1))

    with pytest.raises(EvidenceTrustError, match="revoked"):
        verify_and_consume_evidence(conn, evidence, proof, expected_audience=AUDIENCE, now=NOW)


def test_expired_attestation_is_rejected():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(
        evidence, private_key,
        issued_at=NOW - timedelta(minutes=4),
        expires_at=NOW - timedelta(minutes=3),
    )
    conn = verifier(private_key)

    with pytest.raises(EvidenceTrustError, match="expired"):
        verify_and_consume_evidence(conn, evidence, proof, expected_audience=AUDIENCE, now=NOW)


def test_future_issued_attestation_is_rejected():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(
        evidence, private_key,
        issued_at=NOW + timedelta(minutes=5),
        expires_at=NOW + timedelta(minutes=6),
    )
    conn = verifier(private_key)

    with pytest.raises(EvidenceTrustError, match="future"):
        verify_and_consume_evidence(conn, evidence, proof, expected_audience=AUDIENCE, now=NOW)


def test_signature_is_bound_to_tenant_task_epoch_and_run_id():
    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key)
    forged = replace(proof, lease_epoch=99)
    conn = verifier(private_key)

    with pytest.raises(EvidenceTrustError, match="do not bind"):
        verify_and_consume_evidence(conn, evidence, forged, expected_audience=AUDIENCE, now=NOW)
    assert not conn.nonces



def test_stale_evidence_observation_is_rejected_even_with_fresh_attestation():
    private_key = Ed25519PrivateKey.generate()
    evidence = EvidenceEnvelope.create(
        evidence_id="old-event", tenant_id=TENANT, task_id="task-old",
        source="temporal_history",
        observed_at=(NOW-timedelta(hours=1)).isoformat(),
        payload={"execution_status": "COMPLETED"},
        workflow_id="workflow-old", run_id="run-old",
    )
    proof = sample_attestation(evidence, private_key)
    conn = verifier(private_key)

    with pytest.raises(EvidenceTrustError, match="observation is stale"):
        verify_and_consume_evidence(conn, evidence, proof, expected_audience=AUDIENCE, now=NOW)
    assert not conn.nonces


def test_trusted_adjudication_verifies_attestation_before_using_evidence():
    from agent_memory_core.evidence_trust import adjudicate_trusted_evidence

    private_key = Ed25519PrivateKey.generate()
    evidence = sample_evidence()
    proof = sample_attestation(evidence, private_key)
    conn = FakeConnection(
        public_key=export_public_key(private_key),
        allowed_sources=["temporal_history"],
    )

    result = adjudicate_trusted_evidence(
        conn, [(evidence, proof)], expected_audience=AUDIENCE, now=NOW,
    )

    assert result.adjudication.outcome == "IN_PROGRESS"
    assert len(result.verified_evidence) == 1
    assert result.verified_evidence[0].issuer == ISSUER
