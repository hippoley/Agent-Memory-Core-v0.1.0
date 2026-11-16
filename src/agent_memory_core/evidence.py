"""Domain-neutral, fail-closed evidence handling for LLM memory revisions.

Retrieval discovers candidates; it does not authorize deleting memories.
Only an exact tenant-scoped canonical claim can authorize a model's proposed
RETRACT. The store must recheck the expected value when applying the change.
"""
from __future__ import annotations

import json


def evidence_from_hits(hits, *, limit: int = 8) -> list[dict]:
    if not 1 <= limit <= 20:
        raise ValueError("Evidence limit out of range")
    result = []
    for hit in hits[:limit]:
        if isinstance(hit, dict):
            memory_id = hit.get("memory_id") or hit.get("id")
            subject = hit.get("subject_id")
            predicate = hit.get("predicate")
            value = hit.get("value")
        else:
            memory_id = getattr(hit, "memory_id", None)
            subject = getattr(hit, "subject_id", None)
            predicate = getattr(hit, "predicate", None)
            value = getattr(hit, "value", None)
        if (not isinstance(memory_id, str) or not memory_id
                or not isinstance(subject, str) or not subject
                or not isinstance(predicate, str) or not predicate
                or not isinstance(value, dict)):
            raise ValueError("Invalid canonical evidence")
        result.append({
            "memory_id": memory_id, "subject_id": subject,
            "predicate": predicate, "value": value
        })
    return result


def guard_retractions(candidates: list[dict], evidence: list[dict]) -> list[dict]:
    """Validate model withdrawal proposals against supplied canonical evidence.

    A RETRACT must reference the exact canonical memory ID, subject, predicate
    and expected old JSON value, and cannot target two claims. This is a
    precondition, not authorization; PostgreSQL still checks the latest value.
    UPSERT is allowed to create novel domain facts and is not vocabulary bound.
    """
    if not isinstance(candidates, list):
        raise ValueError("Extractor must return a candidate list")
    by_id = {e["memory_id"]: e for e in evidence}
    result = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("Extractor returned a non-object candidate")
        if candidate.get("operation", "UPSERT") != "RETRACT":
            result.append(candidate)
            continue
        memory_id = candidate.get("expected_memory_id")
        if not isinstance(memory_id, str) or memory_id not in by_id:
            raise ValueError("Unverified memory withdrawal: missing canonical memory reference")
        known = by_id[memory_id]
        # Caller text is untrusted. Canonical subject/predicate/value must all
        # match exact evidence; no entity alias expansion or fuzzy match here.
        if (candidate.get("subject_id") != known["subject_id"]
                or candidate.get("subject_name") is not None
                or candidate.get("predicate") != known["predicate"]
                or json.dumps(candidate.get("expected_value"), sort_keys=True, ensure_ascii=False)
                    != json.dumps(known["value"], sort_keys=True, ensure_ascii=False)):
            raise ValueError("Unverified memory withdrawal: candidate differs from evidence")
        # Preserve the canonical claim ID as an additional database-level
        # precondition; checking only value would allow stale same-value claims.
        result.append(candidate)
    return result
