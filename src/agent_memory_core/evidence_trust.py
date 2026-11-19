"""Authenticated provenance and replay protection for durable evidence.

This module verifies producer authority and Ed25519 signatures against a
PostgreSQL trust registry. It does not collect evidence or perform recovery.
Private keys must remain outside this service, ideally in producer-side KMS/HSM.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Mapping

from .durable_evidence import EvidenceEnvelope, EvidenceValidationError, _canonical_json, _parse_timestamp


class EvidenceTrustError(ValueError):
    """Evidence is not authenticated or is outside the producer's authority."""


class EvidenceReplayError(EvidenceTrustError):
    """The signed nonce has already been consumed."""


@dataclass(frozen=True)
class EvidenceAttestation:
    schema_version: str
    issuer: str
    key_id: str
    audience: str
    issued_at: str
    expires_at: str
    nonce: str
    evidence_sha256: str
    tenant_id: str
    task_id: str
    source: str
    workflow_id: str | None
    run_id: str | None
    lease_epoch: int | None
    signature: str

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "issuer": self.issuer,
            "key_id": self.key_id,
            "audience": self.audience,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
            "evidence_sha256": self.evidence_sha256,
            "tenant_id": self.tenant_id,
            "task_id": self.task_id,
            "source": self.source,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "lease_epoch": self.lease_epoch,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_dict(), "signature": self.signature}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceAttestation":
        try:
            return cls(**dict(value))
        except (TypeError, ValueError) as exc:
            raise EvidenceTrustError("invalid evidence attestation fields") from exc


@dataclass(frozen=True)
class VerifiedEvidence:
    evidence: EvidenceEnvelope
    issuer: str
    key_id: str
    audience: str
    issued_at: str
    expires_at: str
    nonce: str
    signature_sha256: str


def _crypto():
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    except ImportError as exc:
        raise RuntimeError("Install the evidence extra: pip install agent-memory-core[evidence]") from exc
    return InvalidSignature, Ed25519PrivateKey, Ed25519PublicKey


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise EvidenceTrustError("signature must be a non-empty base64url string")
    try:
        raw = base64.b64decode(
            value.encode("ascii") + b"=" * (-len(value) % 4),
            altchars=b"-_", validate=True,
        )
    except (ValueError, UnicodeEncodeError) as exc:
        raise EvidenceTrustError("signature is not valid base64url") from exc
    if len(raw) != 64:
        raise EvidenceTrustError("Ed25519 signature must contain 64 bytes")
    return raw


def sign_evidence(
    evidence: EvidenceEnvelope, *, issuer: str, key_id: str, audience: str,
    issued_at: str, expires_at: str, nonce: str, private_key: Any,
) -> EvidenceAttestation:
    """Create an attestation using a producer-held Ed25519 private key."""
    evidence.verify()
    _parse_timestamp(issued_at)
    _parse_timestamp(expires_at)
    if not all(isinstance(v, str) and v for v in (issuer, key_id, audience, nonce)):
        raise EvidenceTrustError("issuer, key_id, audience and nonce are required")
    if len(nonce) < 16:
        raise EvidenceTrustError("nonce must contain at least 16 characters")
    claims = {
        "schema_version": "amc.evidence-attestation.v1",
        "issuer": issuer,
        "key_id": key_id,
        "audience": audience,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "nonce": nonce,
        "evidence_sha256": evidence.sha256,
        "tenant_id": evidence.tenant_id,
        "task_id": evidence.task_id,
        "source": evidence.source,
        "workflow_id": evidence.workflow_id,
        "run_id": evidence.run_id,
        "lease_epoch": evidence.lease_epoch,
    }
    signature = private_key.sign(_canonical_json(claims).encode("utf-8"))
    return EvidenceAttestation(**claims, signature=_b64url_encode(signature))


def verify_and_consume_evidence(
    conn: Any, evidence: EvidenceEnvelope | Mapping[str, Any],
    attestation: EvidenceAttestation | Mapping[str, Any], *,
    expected_audience: str, now: datetime | None = None,
    max_clock_skew: timedelta = timedelta(seconds=30),
    max_attestation_lifetime: timedelta = timedelta(minutes=5),
    max_evidence_age: timedelta = timedelta(minutes=15),
) -> VerifiedEvidence:
    """Verify authority, signature, freshness and nonce uniqueness atomically.

    The trust row is locked FOR SHARE while validating and consuming the nonce,
    so a concurrent revocation cannot race this verification transaction.
    """
    item = evidence if isinstance(evidence, EvidenceEnvelope) else EvidenceEnvelope.from_dict(evidence)
    item.verify()
    proof = attestation if isinstance(attestation, EvidenceAttestation) else EvidenceAttestation.from_dict(attestation)
    if proof.schema_version != "amc.evidence-attestation.v1":
        raise EvidenceTrustError("unsupported attestation schema version")
    if not expected_audience or proof.audience != expected_audience:
        raise EvidenceTrustError("attestation audience mismatch")
    if not all(isinstance(v, str) and v for v in (proof.issuer, proof.key_id, proof.nonce)):
        raise EvidenceTrustError("issuer, key_id and nonce are required")
    if len(proof.nonce) < 16:
        raise EvidenceTrustError("nonce must contain at least 16 characters")
    if (
        proof.evidence_sha256 != item.sha256
        or proof.tenant_id != item.tenant_id
        or proof.task_id != item.task_id
        or proof.source != item.source
        or proof.workflow_id != item.workflow_id
        or proof.run_id != item.run_id
        or proof.lease_epoch != item.lease_epoch
    ):
        raise EvidenceTrustError("attestation claims do not bind to this exact evidence envelope")

    issued = _parse_timestamp(proof.issued_at)
    expires = _parse_timestamp(proof.expires_at)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise EvidenceTrustError("verification time must be timezone-aware")
    if max_clock_skew < timedelta(0) or max_attestation_lifetime <= timedelta(0) or max_evidence_age <= timedelta(0):
        raise EvidenceTrustError("freshness policy durations must be positive")
    observed = _parse_timestamp(item.observed_at)
    if observed > current + max_clock_skew:
        raise EvidenceTrustError("evidence observation time is too far in the future")
    if current - observed > max_evidence_age + max_clock_skew:
        raise EvidenceTrustError("evidence observation is stale under the configured freshness policy")
    if expires <= issued or expires - issued > max_attestation_lifetime:
        raise EvidenceTrustError("attestation lifetime is invalid or exceeds policy")
    if issued > current + max_clock_skew:
        raise EvidenceTrustError("attestation was issued too far in the future")
    if expires <= current - max_clock_skew:
        raise EvidenceTrustError("attestation has expired")

    InvalidSignature, _, Ed25519PublicKey = _crypto()
    try:
        signature = _b64url_decode(proof.signature)
    except EvidenceTrustError:
        raise

    with conn.transaction():
        row = conn.execute(
            """SELECT public_key, allowed_sources, allowed_tenants, audience,
                      not_before, expires_at, revoked_at
               FROM memory_evidence_producer_keys
               WHERE issuer=%s AND key_id=%s
               FOR SHARE""",
            (proof.issuer, proof.key_id),
        ).fetchone()
        if row is None:
            raise EvidenceTrustError("producer key is not trusted")
        public_key, allowed_sources, allowed_tenants, audience, not_before, key_expires, revoked_at = row
        if revoked_at is not None:
            raise EvidenceTrustError("producer key has been revoked")
        if audience != proof.audience:
            raise EvidenceTrustError("producer key is not authorized for this audience")
        if item.source not in set(allowed_sources or []):
            raise EvidenceTrustError("producer key is not authorized for this evidence source")
        if "*" not in set(allowed_tenants or []) and item.tenant_id not in set(allowed_tenants or []):
            raise EvidenceTrustError("producer key is not authorized for this tenant")
        if issued < not_before or (key_expires is not None and issued >= key_expires):
            raise EvidenceTrustError("attestation was issued outside the producer key validity window")
        if key_expires is not None and current >= key_expires:
            raise EvidenceTrustError("producer key has expired")

        try:
            verifier = Ed25519PublicKey.from_public_bytes(bytes(public_key))
            verifier.verify(signature, _canonical_json(proof.unsigned_dict()).encode("utf-8"))
        except (InvalidSignature, ValueError) as exc:
            raise EvidenceTrustError("Ed25519 signature verification failed") from exc

        consumed = conn.execute(
            """INSERT INTO memory_evidence_nonces
                 (issuer,key_id,nonce,evidence_sha256,tenant_id,task_id,issued_at,expires_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (issuer,key_id,nonce) DO NOTHING
               RETURNING nonce""",
            (proof.issuer, proof.key_id, proof.nonce, item.sha256,
             item.tenant_id, item.task_id, issued, expires),
        ).fetchone()
        if consumed is None:
            raise EvidenceReplayError("attestation nonce has already been consumed")

    return VerifiedEvidence(
        evidence=item, issuer=proof.issuer, key_id=proof.key_id,
        audience=proof.audience, issued_at=proof.issued_at,
        expires_at=proof.expires_at, nonce=proof.nonce,
        signature_sha256=hashlib.sha256(signature).hexdigest(),
    )


def export_public_key(private_key: Any) -> bytes:
    """Return the raw 32-byte Ed25519 public key for trusted-key provisioning."""
    _, _, _ = _crypto()
    from cryptography.hazmat.primitives import serialization
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
