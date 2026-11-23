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
    seen = {}
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
        row = {"memory_id": memory_id, "subject_id": subject,
               "predicate": predicate, "value": value}
        previous = seen.get(memory_id)
        if previous is not None:
            if json.dumps(previous, sort_keys=True, ensure_ascii=False, allow_nan=False) != json.dumps(
                    row, sort_keys=True, ensure_ascii=False, allow_nan=False):
                raise ValueError("Conflicting canonical evidence for the same memory_id")
            continue
        seen[memory_id] = row
        result.append(row)
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
    by_id = {}
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get("memory_id"), str):
            raise ValueError("Invalid canonical withdrawal evidence")
        memory_id = item["memory_id"]
        previous = by_id.get(memory_id)
        if previous is not None and previous != item:
            raise ValueError("Conflicting canonical evidence for the same memory_id")
        by_id[memory_id] = item
    # Validate the *whole* model response before the default non-atomic
    # processor can apply any candidate. An invalid later operation, or a
    # simultaneous UPSERT/RETRACT for one fact, must not retire a claim first.
    operations_by_fact = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("Extractor returned a non-object candidate")
        operation = candidate.get("operation", "UPSERT")
        if operation not in ("UPSERT", "RETRACT"):
            raise ValueError("Unsupported grounded memory operation")
        subject, predicate = candidate.get("subject_id"), candidate.get("predicate")
        if isinstance(subject, str) and subject and isinstance(predicate, str) and predicate:
            key = (subject, predicate)
            prior = operations_by_fact.setdefault(key, operation)
            if prior != operation:
                raise ValueError("Conflicting UPSERT and RETRACT for the same canonical fact")

    result = []
    withdrawn_ids = set()
    for candidate in candidates:
        if candidate.get("operation", "UPSERT") != "RETRACT":
            result.append(candidate)
            continue
        memory_id = candidate.get("expected_memory_id")
        if not isinstance(memory_id, str) or memory_id not in by_id:
            raise ValueError("Unverified memory withdrawal: missing canonical memory reference")
        # A single model extraction response must not withdraw the same
        # canonical claim twice. Reject the entire proposed batch BEFORE
        # MemoryProcessor can apply any of its items.
        if memory_id in withdrawn_ids:
            raise ValueError("Duplicate withdrawal target in one candidate batch")
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
        withdrawn_ids.add(memory_id)
        result.append(candidate)
    return result
