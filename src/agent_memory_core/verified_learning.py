"""Verified Learning Handoff: independent evidence gate over an AMC context pack.

Inspired by Anthropic's 2026-11-06 discussion of memory correction and
consolidation. This is NOT an implementation of Claude Dreaming.

The model proposes a learned claim; this gate compares it against an
independently supplied, current source snapshot. A matching source can
authorize read/planning use, NEVER execution. Only a trusted host may supply
the current source snapshot (and verify the source's authenticity); untrusted
Agent output must not populate it.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping


READ_USES = frozenset({"answer", "plan"})
EXECUTION_USES = frozenset({"execute", "approve", "pay", "device_control"})


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class TrustedSource:
    """A snapshot injected by a trusted, non-Agent source adapter.

    source_id identifies the exact immutable revision, not a mutable URL.
    The digest protects the snapshot within this evaluation; callers must
    authenticate the source *before* constructing this object.
    """

    source_id: str
    facts: Mapping[str, Any]
    sha256: str

    @classmethod
    def from_verified_document(cls, source_id: str, facts: Mapping[str, Any]) -> "TrustedSource":
        if not isinstance(source_id, str) or not source_id or not isinstance(facts, Mapping):
            raise ValueError("Invalid trusted source")
        copied = dict(facts)
        return cls(source_id=source_id, facts=copied,
                   sha256=sha256(_canonical({"source_id": source_id,
                                             "facts": copied})).hexdigest())

    def intact(self) -> bool:
        return self.sha256 == sha256(_canonical(
            {"source_id": self.source_id, "facts": self.facts}
        )).hexdigest()


@dataclass(frozen=True)
class LearningDecision:
    state: str
    reason: str
    claim_id: str | None
    source_id: str | None
    evidence_sha256: str | None
    allowed_uses: tuple[str, ...]
    requires_new_handoff: bool = False

    @property
    def permitted(self) -> bool:
        return bool(self.allowed_uses)


def review_handoff_claim(*, accepted_handoff: Mapping[str, Any],
                         subject_id: str, predicate: str, proposed_value: Any,
                         trusted_current_source: TrustedSource | None,
                         requested_use: str = "answer") -> LearningDecision:
    """Check learning against a current independent source before reuse.

    No database writes, model-based judgments, execution, or implicit
    verification promotion. The AMC API must authenticate and accept the
    handoff before the trusted host passes its response to this function.
    """
    def deny(state: str, reason: str, claim_id=None, source_id=None,
             reoffer=False) -> LearningDecision:
        return LearningDecision(state, reason, claim_id, source_id, None, (),
                                requires_new_handoff=reoffer)

    if requested_use not in READ_USES | EXECUTION_USES:
        return deny("DENIED", "UNSUPPORTED_USE")
    if not isinstance(accepted_handoff, Mapping) or accepted_handoff.get("status") != "accepted":
        return deny("DENIED", "HANDOFF_NOT_ACCEPTED")
    pack = accepted_handoff.get("payload")
    if not isinstance(pack, Mapping) or pack.get("schema") != "amc.context-pack.v1":
        return deny("DENIED", "INVALID_HANDOFF_PAYLOAD")
    if pack.get("omitted_claim_ids") or pack.get("unresolved_effects"):
        return deny("DENIED", "INCOMPLETE_OR_UNRESOLVED_CONTEXT")
    if subject_id not in pack.get("subjects", []):
        return deny("DENIED", "SUBJECT_NOT_IN_HANDOFF")
    candidates = [
        c for c in pack.get("claims", [])
        if isinstance(c, Mapping) and c.get("subject_id") == subject_id
        and c.get("predicate") == predicate
    ]
    if len(candidates) != 1:
        return deny("DENIED", "AMBIGUOUS_OR_MISSING_CLAIM")
    claim = candidates[0]
    claim_id, claim_source = claim.get("id"), claim.get("source_id")
    if proposed_value != claim.get("value"):
        return deny("DENIED", "PROPOSAL_DIFFERS_FROM_HANDOFF", claim_id, claim_source)
    if trusted_current_source is None:
        return deny("UNVERIFIED", "INDEPENDENT_SOURCE_UNAVAILABLE", claim_id, claim_source)
    if not trusted_current_source.intact():
        return deny("DENIED", "SOURCE_SNAPSHOT_CORRUPT", claim_id, claim_source)
    if claim_source != trusted_current_source.source_id:
        return deny("STALE", "SOURCE_REVISION_CHANGED", claim_id, claim_source,
                    reoffer=True)
    if predicate not in trusted_current_source.facts:
        return deny("UNVERIFIED", "CLAIM_NOT_TESTABLE_FROM_SOURCE", claim_id, claim_source)
    if proposed_value != trusted_current_source.facts[predicate]:
        return deny("CONFLICT", "SOURCE_DISAGREES_WITH_CLAIM", claim_id, claim_source,
                    reoffer=True)
    if requested_use not in READ_USES:
        return deny("VERIFIED", "EXECUTION_REQUIRES_SEPARATE_AUTHORITY",
                    claim_id, claim_source)
    return LearningDecision(
        state="VERIFIED", reason="INDEPENDENT_SOURCE_MATCH", claim_id=claim_id,
        source_id=claim_source, evidence_sha256=trusted_current_source.sha256,
        allowed_uses=(requested_use,),
    )
