"""Source authenticity boundary: signed issuer vs. self-consistent Agent fiction."""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_memory_core.source_attestation import (
    SourceTrustPolicy, SourceAttestationError,
    verify_source_envelope, review_signed_handoff_claim,
)

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
TENANT = "tenant-a"
SUBJECT = "supplier-001"
PREDICATE = "payment_period"
SOURCE = "contract:v3"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def keypair():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private, public


def envelope(private, **overrides):
    body = {
        "schema": "amc.source-attestation.v1",
        "issuer": "contract-registry",
        "tenant_id": TENANT,
        "subject_id": SUBJECT,
        "predicate": PREDICATE,
        "source_id": SOURCE,
        "value": {"period": "quarterly"},
        "issued_at": (NOW - timedelta(minutes=1)).isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
    }
    body.update(overrides)
    return {
        "body": body,
        "signature": base64.urlsafe_b64encode(private.sign(canonical(body)))
                     .decode("ascii").rstrip("="),
    }


def policy(public, **overrides):
    fields = {
        "issuer_public_keys": {"contract-registry": public},
        "active_revisions": {(TENANT, SUBJECT, PREDICATE): SOURCE},
    }
    fields.update(overrides)
    return SourceTrustPolicy(**fields)


def accepted_packet():
    pack = {
        "schema": "amc.context-pack.v1",
        "subjects": [SUBJECT],
        "claims": [{
            "id": "claim-v3", "subject_id": SUBJECT,
            "predicate": PREDICATE, "source_id": SOURCE,
            "value": {"period": "quarterly"},
        }],
        "omitted_claim_ids": [],
        "unresolved_effects": [],
    }
    pack["sha256"] = hashlib.sha256(canonical(pack)).hexdigest()
    return {"status": "accepted", "payload": pack}


def verify(doc, trust, **kwargs):
    return verify_source_envelope(
        envelope=doc, policy=trust, tenant_id=TENANT,
        subject_id=SUBJECT, predicate=PREDICATE, now=NOW, **kwargs)


def test_trusted_issuer_allows_read_not_execution():
    signer, public = keypair()
    doc, trust = envelope(signer), policy(public)
    source = verify(doc, trust)
    assert source.source_id == SOURCE
    assert source.facts[PREDICATE] == {"period": "quarterly"}
    for use in ("answer", "plan"):
        result = review_signed_handoff_claim(
            accepted_handoff=accepted_packet(), subject_id=SUBJECT,
            predicate=PREDICATE, proposed_value={"period": "quarterly"},
            signed_source_envelope=doc, policy=trust, tenant_id=TENANT,
            requested_use=use, now=NOW)
        assert result.state == "VERIFIED"
        assert result.allowed_uses == (use,)
    refused = review_signed_handoff_claim(
        accepted_handoff=accepted_packet(), subject_id=SUBJECT,
        predicate=PREDICATE, proposed_value={"period": "quarterly"},
        signed_source_envelope=doc, policy=trust, tenant_id=TENANT,
        requested_use="approve", now=NOW)
    assert refused.reason == "EXECUTION_REQUIRES_SEPARATE_AUTHORITY"
    assert not refused.permitted


def test_forged_self_signed_document_with_valid_digest_is_not_trusted():
    signer, public = keypair()
    attack_signer, _ = keypair()
    forged = envelope(attack_signer)
    # This is a perfectly self-consistent signed envelope but NOT signed
    # by the pinned issuer's authorized key.
    with pytest.raises(SourceAttestationError, match="SOURCE_SIGNATURE_INVALID"):
        verify(forged, policy(public))
    legitimate = envelope(signer)
    modified = deepcopy(legitimate)
    modified["body"]["value"] = {"period": "monthly"}
    with pytest.raises(SourceAttestationError, match="SOURCE_SIGNATURE_INVALID"):
        verify(modified, policy(public))


@pytest.mark.parametrize("alter,reason", [
    (lambda p: {"issuer_public_keys": {}}, "ISSUER_NOT_TRUSTED"),
    (lambda p: {"revoked_issuers": frozenset(["contract-registry"])}, "SOURCE_REVOKED"),
    (lambda p: {"revoked_sources": frozenset([SOURCE])}, "SOURCE_REVOKED"),
    (lambda p: {"active_revisions": {(TENANT, SUBJECT, PREDICATE): "contract:v4"}},
     "SOURCE_REVISION_NOT_ACTIVE"),
    (lambda p: {"active_revisions": {}}, "SOURCE_REVISION_NOT_ACTIVE"),
])
def test_issuer_revocation_and_host_current_revision_gate(alter, reason):
    signer, public = keypair()
    doc = envelope(signer)
    trust = policy(public, **alter(public))
    with pytest.raises(SourceAttestationError, match=reason):
        verify(doc, trust)


def test_wrong_tenant_scope_and_other_predicate_are_rejected():
    signer, public = keypair()
    doc, trust = envelope(signer), policy(public)
    with pytest.raises(SourceAttestationError, match="SOURCE_SCOPE_MISMATCH"):
        verify_source_envelope(envelope=doc, policy=trust, tenant_id="tenant-b",
                               subject_id=SUBJECT, predicate=PREDICATE, now=NOW)
    with pytest.raises(SourceAttestationError, match="SOURCE_SCOPE_MISMATCH"):
        verify_source_envelope(envelope=doc, policy=trust, tenant_id=TENANT,
                               subject_id=SUBJECT, predicate="payment_iban", now=NOW)


@pytest.mark.parametrize("times", [
    {"expires_at": (NOW - timedelta(seconds=1)).isoformat()},
    {"issued_at": (NOW + timedelta(minutes=2)).isoformat()},
    {"expires_at": (NOW - timedelta(minutes=2)).isoformat()},
])
def test_expired_future_dated_or_inverted_time_window(times):
    signer, public = keypair()
    with pytest.raises(SourceAttestationError, match="SOURCE_NOT_CURRENT"):
        verify(envelope(signer, **times), policy(public))


def test_revocation_changes_decision_without_modifying_signed_document():
    signer, public = keypair()
    doc = envelope(signer)
    good = review_signed_handoff_claim(
        accepted_handoff=accepted_packet(), subject_id=SUBJECT,
        predicate=PREDICATE, proposed_value={"period": "quarterly"},
        signed_source_envelope=doc, policy=policy(public), tenant_id=TENANT,
        now=NOW)
    assert good.permitted
    revoked = review_signed_handoff_claim(
        accepted_handoff=accepted_packet(), subject_id=SUBJECT,
        predicate=PREDICATE, proposed_value={"period": "quarterly"},
        signed_source_envelope=doc, policy=policy(
            public, revoked_sources=frozenset({SOURCE})),
        tenant_id=TENANT, now=NOW)
    assert revoked.reason == "SOURCE_REVOKED"
    assert not revoked.permitted


def test_bad_signature_encoding_fails_closed():
    signer, public = keypair()
    doc = envelope(signer)
    doc["signature"] = "not valid!?"
    with pytest.raises(SourceAttestationError, match="INVALID_SIGNATURE_ENCODING"):
        verify(doc, policy(public))


def test_recomputed_context_hash_cannot_override_source_signature():
    signer, public = keypair()
    attack_signer, _ = keypair()
    forged_packet = accepted_packet()
    forged_packet["payload"]["claims"][0]["value"] = {"period": "monthly"}
    old_sha = forged_packet["payload"].pop("sha256")
    forged_packet["payload"]["sha256"] = hashlib.sha256(
        canonical(forged_packet["payload"])).hexdigest()
    assert forged_packet["payload"]["sha256"] != old_sha
    decision = review_signed_handoff_claim(
        accepted_handoff=forged_packet, subject_id=SUBJECT,
        predicate=PREDICATE, proposed_value={"period": "monthly"},
        signed_source_envelope=envelope(attack_signer, value={"period": "monthly"}),
        policy=policy(public), tenant_id=TENANT, now=NOW)
    assert decision.reason == "SOURCE_SIGNATURE_INVALID"
    assert not decision.permitted
