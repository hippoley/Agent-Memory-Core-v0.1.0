"""Server-owned Postgres source trust authority, reusing Evidence Trust keys.

Do not call this with a database connection controlled by an Agent.
The database login MUST be an authorized, restricted service principal;
database owner/superuser is only for migration and integration fixtures.
The committed revision and key rows are held FOR SHARE across decision,
serializing against authorized revocation/revision changes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .source_attestation import (
    SourceAttestationError, SourceTrustPolicy, review_signed_handoff_claim,
)
from .verified_learning import LearningDecision


def review_registry_handoff_claim(
    *, conn: Any, accepted_handoff: Mapping[str, Any],
    signed_source_envelope: Mapping[str, Any],
    tenant_id: str, subject_id: str, predicate: str,
    proposed_value: Any, requested_use: str = "answer",
    now: datetime | None = None,
) -> LearningDecision:
    """Load trusted keys / active revision from DB, never from Agent JSON.

    The PostgreSQL role and its ability to read *only* appropriate scopes
    must be enforced outside this function. Tenant and principal status,
    key validity, revocation and active revision are checked inside one
    transaction. The external signed source must be checked again for
    each new use, not cached across revocations.
    """
    if not all(isinstance(x, str) and x for x in (tenant_id, subject_id, predicate)):
        raise SourceAttestationError("INVALID_SCOPE")
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise SourceAttestationError("NAIVE_CLOCK")
    instant = instant.astimezone(timezone.utc)
    with conn.transaction():
        row = conn.execute(
            """SELECT revision.source_id, revision.issuer, producer.key_id,
                      producer.public_key
               FROM memory_knowledge_source_revisions revision
               JOIN memory_tenants tenant
                 ON tenant.tenant_id=revision.tenant_id
               JOIN memory_evidence_producer_keys producer
                 ON producer.issuer=revision.issuer
                AND producer.key_id=revision.key_id
               JOIN memory_principals principal
                 ON principal.principal_id=producer.principal_id
               WHERE revision.tenant_id=%s
                 AND revision.subject_id=%s
                 AND revision.predicate=%s
                 AND revision.revoked_at IS NULL
                 AND tenant.status='active'
                 AND principal.status='active'
                 AND principal.principal_type='service'
                 AND producer.revoked_at IS NULL
                 AND producer.audience='amc-knowledge-source'
                 AND producer.not_before <= %s::timestamptz
                 AND (producer.expires_at IS NULL OR producer.expires_at > %s::timestamptz)
               FOR SHARE OF revision,tenant,producer,principal""",
            (tenant_id, subject_id, predicate, instant, instant),
        ).fetchone()
        if row is None:
            return LearningDecision("DENIED", "SOURCE_AUTHORITY_NOT_ACTIVE",
                                    None, None, None, ())
        source_id, issuer, _, key_bytes = row
        if isinstance(key_bytes, memoryview):
            key_bytes = bytes(key_bytes)
        if not isinstance(key_bytes, bytes) or len(key_bytes) != 32:
            return LearningDecision("DENIED", "SOURCE_KEY_INVALID",
                                    None, None, None, ())
        policy = SourceTrustPolicy(
            issuer_public_keys={issuer: key_bytes},
            active_revisions={(tenant_id, subject_id, predicate): source_id},
        )
        # Key + revision locks stay held until the completed decision.
        return review_signed_handoff_claim(
            accepted_handoff=accepted_handoff,
            signed_source_envelope=signed_source_envelope,
            policy=policy, tenant_id=tenant_id,
            subject_id=subject_id, predicate=predicate,
            proposed_value=proposed_value,
            requested_use=requested_use, now=instant,
        )
