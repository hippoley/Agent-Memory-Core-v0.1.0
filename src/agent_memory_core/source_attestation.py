"""Opt-in authenticated source envelope for Verified Learning Handoff.

The issuer signs the exact knowledge claim, tenant, source revision and
expiry with Ed25519. The *host* provisions pinned issuer public keys,
current source revisions and revocations: never accept a trust policy from
the Agent, the signed envelope, or model-generated context.

This module is an experimental adapter, not a certificate authority,
online revocation service or provider execution authorization.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Mapping

from .verified_learning import LearningDecision, TrustedSource, review_handoff_claim


class SourceAttestationError(ValueError):
    """No trusted knowledge may be returned for this envelope."""


def _bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _date(value: str) -> datetime:
    if not isinstance(value, str):
        raise SourceAttestationError("INVALID_SOURCE_TIME")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceAttestationError("INVALID_SOURCE_TIME") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceAttestationError("NAIVE_SOURCE_TIME")
    return parsed.astimezone(timezone.utc)


def _b64decode(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise SourceAttestationError("INVALID_SIGNATURE_ENCODING")
    try:
        if len(value) % 4 == 1:
            raise ValueError("Bad length")
        # Explicit alphabet check: Python's default base64 decode is lenient.
        if any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in value):
            raise ValueError("Invalid alphabet")
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, base64.binascii.Error) as exc:
        raise SourceAttestationError("INVALID_SIGNATURE_ENCODING") from exc


@dataclass(frozen=True)
class SourceTrustPolicy:
    """Host-controlled, fresh policy; never deserialize from Agent input.

    active_revisions maps (tenant_id, subject_id, predicate) -> source_id.
    A revision not explicitly listed is NOT current.
    """
    issuer_public_keys: Mapping[str, bytes]
    active_revisions: Mapping[tuple[str, str, str], str]
    revoked_issuers: frozenset[str] = field(default_factory=frozenset)
    revoked_sources: frozenset[str] = field(default_factory=frozenset)


def verify_source_envelope(*, envelope: Mapping[str, Any],
                           policy: SourceTrustPolicy,
                           tenant_id: str, subject_id: str,
                           predicate: str,
                           now: datetime | None = None) -> TrustedSource:
    """Verify Ed25519 source identity and host-pinned revision before reuse."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise RuntimeError("Install AMC's evidence extra for Ed25519 verification") from exc

    if not isinstance(envelope, Mapping) or set(envelope) != {"body", "signature"}:
        raise SourceAttestationError("INVALID_ENVELOPE")
    body = envelope["body"]
    if not isinstance(body, Mapping) or set(body) != {
        "schema", "issuer", "tenant_id", "subject_id", "predicate", "source_id",
        "value", "issued_at", "expires_at",
    }:
        raise SourceAttestationError("INVALID_SOURCE_BODY")
    if body["schema"] != "amc.source-attestation.v1":
        raise SourceAttestationError("UNKNOWN_ATTESTATION_SCHEMA")
    for key, expected in (("tenant_id", tenant_id), ("subject_id", subject_id),
                          ("predicate", predicate)):
        if not isinstance(expected, str) or not expected or body[key] != expected:
            raise SourceAttestationError("SOURCE_SCOPE_MISMATCH")
    issuer, source_id = body["issuer"], body["source_id"]
    if not isinstance(issuer, str) or not issuer or not isinstance(source_id, str) or not source_id:
        raise SourceAttestationError("INVALID_SOURCE_IDENTITY")
    if issuer in policy.revoked_issuers or source_id in policy.revoked_sources:
        raise SourceAttestationError("SOURCE_REVOKED")
    public_key = policy.issuer_public_keys.get(issuer)
    if not isinstance(public_key, bytes) or len(public_key) != 32:
        raise SourceAttestationError("ISSUER_NOT_TRUSTED")
    signature = _b64decode(envelope["signature"])
    if len(signature) != 64:
        raise SourceAttestationError("INVALID_SIGNATURE_LENGTH")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, _bytes(body))
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise SourceAttestationError("SOURCE_SIGNATURE_INVALID") from exc

    issued, expires = _date(body["issued_at"]), _date(body["expires_at"])
    instant = datetime.now(timezone.utc) if now is None else now
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise SourceAttestationError("NAIVE_CLOCK")
    instant = instant.astimezone(timezone.utc)
    if issued > instant + timedelta(seconds=60) or expires <= issued or expires <= instant:
        raise SourceAttestationError("SOURCE_NOT_CURRENT")
    if policy.active_revisions.get((tenant_id, subject_id, predicate)) != source_id:
        raise SourceAttestationError("SOURCE_REVISION_NOT_ACTIVE")
    # No second, caller-controlled facts dictionary: the signed value is
    # the *only* source-of-truth passed to the existing comparison gate.
    return TrustedSource.from_verified_document(source_id, {predicate: body["value"]})


def review_signed_handoff_claim(*, accepted_handoff: Mapping[str, Any],
                                subject_id: str, predicate: str,
                                proposed_value: Any,
                                signed_source_envelope: Mapping[str, Any],
                                policy: SourceTrustPolicy,
                                tenant_id: str, requested_use: str = "answer",
                                now: datetime | None = None) -> LearningDecision:
    """Only the host can pass pinned keys and fresh active-revision policy.

    This read-only authorization *advice* must never be used in place of
    the independent fenced worker's side-effect permission.
    """
    try:
        source = verify_source_envelope(
            envelope=signed_source_envelope, policy=policy,
            tenant_id=tenant_id, subject_id=subject_id,
            predicate=predicate, now=now)
    except SourceAttestationError as exc:
        return LearningDecision(
            state="DENIED", reason=str(exc), claim_id=None, source_id=None,
            evidence_sha256=None, allowed_uses=(),
        )
    return review_handoff_claim(
        accepted_handoff=accepted_handoff, subject_id=subject_id,
        predicate=predicate, proposed_value=proposed_value,
        trusted_current_source=source, requested_use=requested_use)
