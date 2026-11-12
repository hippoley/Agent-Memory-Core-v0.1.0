"""Model-agnostic memory extraction, deduplication and conflict application.

The model/extractor is deliberately injected: this package does not require a
cloud LLM. The deterministic processing stage validates candidates, deduplicates
identical active facts, classifies the relation of changed facts (contradiction
versus mergeable complement), consolidates mergeable facts, versions conflicts,
and optionally indexes vectors.

Relation vocabulary (deterministic, no model in the loop):
- CREATED       no active claim for the same subject/predicate
- DUPLICATE     exact canonical match; provenance is linked, nothing rewritten
- SUPERSEDES    same subject/predicate, values are not mergeable; the claim is
                versioned forward and the old one is superseded
- CONTRADICTION same subject/predicate, overlapping keys disagree on scalar
                values; recorded as an explicit contradiction while replacing
- MERGED        candidate was consolidated into the active claim (merge=True
                requested and values were mergeable)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import inspect
import json
import math
from typing import Callable

RELATION_CREATED = "CREATED"
RELATION_DUPLICATE = "DUPLICATE"
RELATION_SUPERSEDES = "SUPERSEDES"
RELATION_CONTRADICTION = "CONTRADICTION"
RELATION_MERGED = "MERGED"

def canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def deep_merge(existing, new):
    """Recursively consolidate two JSON values.

    Dicts merge key-wise; lists union preserving order; equal scalars keep.
    Returns None when a scalar conflict makes consolidation impossible.
    Raises TypeError on values that cannot participate in canonical JSON.
    """
    if isinstance(existing, dict) and isinstance(new, dict):
        out = dict(existing)
        for key, item in new.items():
            if key not in out:
                out[key] = item
                continue
            merged = deep_merge(out[key], item)
            if merged is None:
                return None
            out[key] = merged
        return out
    if isinstance(existing, list) and isinstance(new, list):
        out = list(existing)
        for item in new:
            if item not in out:
                out.append(item)
        return out
    if existing == new:
        return existing
    return None


def resolve_conflicts(existing, new):
    """Consolidate with candidate winning on scalar conflicts.

    Like deep_merge, but overlapping scalars take the candidate value instead
    of failing. Used on the CONTRADICTION path so versioned replacement keeps
    non-conflicting knowledge while resolving the disagreement.
    """
    if isinstance(existing, dict) and isinstance(new, dict):
        out = dict(existing)
        for key, item in new.items():
            if key in out and isinstance(out[key], dict) and isinstance(item, dict):
                out[key] = resolve_conflicts(out[key], item)
            else:
                out[key] = item
        return out
    return new


def classify_relation(candidate_value: dict, existing_value: dict) -> str:
    """Classify a candidate against one active claim value (deterministic)."""
    if canonical_json(candidate_value) == canonical_json(existing_value):
        return RELATION_DUPLICATE
    try:
        merged = deep_merge(existing_value, candidate_value)
    except TypeError:
        return RELATION_SUPERSEDES
    if merged is None:
        return RELATION_CONTRADICTION
    if canonical_json(merged) == canonical_json(existing_value):
        # The candidate adds nothing the claim does not already encode.
        return RELATION_DUPLICATE
    return RELATION_MERGED


@dataclass
class ProcessingReport:
    created: list[str] = field(default_factory=list)
    duplicates: int = 0
    rejected: int = 0
    replaced: list[str] = field(default_factory=list)
    embedding_failures: list[str] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)
    ambiguous: int = 0


class MemoryProcessor:
    """Apply structured facts emitted by a caller-provided extraction model.

    Extractor contract: extractor(text) returns an iterable of dictionaries.
    Each candidate has subject_id, predicate, value, and optional confidence.
    A candidate may additionally request consolidation with the active claim
    via "merge": true. An optional embedder receives the normalized candidate
    and returns a (model_id, vector) pair.
    """

    def __init__(self, store, *, confidence_threshold: float = 0.65,
                 embedder: Callable | None = None, entity_resolver: Callable | None = None):
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        self.store = store
        self.confidence_threshold = confidence_threshold
        self.embedder = embedder
        self.entity_resolver = entity_resolver
        atomic = getattr(store, "apply_candidate", None)
        if callable(atomic):
            params = inspect.signature(atomic).parameters
            self._atomic_supports_merge = "merge" in params
            self._atomic_supports_observed = "observed_at" in params
        else:
            self._atomic_supports_merge = False
            self._atomic_supports_observed = False

    @staticmethod
    def _canonical(value: dict) -> str:
        return canonical_json(value)

    def process(self, *, tenant_id: str, text: str, source_id: str,
                extractor: Callable[[str], object],
                observed_at=None) -> ProcessingReport:
        if not tenant_id or not text.strip() or not source_id:
            raise ValueError("tenant_id, non-empty text and source_id are required")
        report = ProcessingReport()
        candidates = extractor(text)
        for candidate in candidates:
            if not isinstance(candidate, dict):
                report.rejected += 1
                continue
            subject = candidate.get("subject_id")
            subject_name = candidate.get("subject_name")
            if subject_name is not None:
                if not isinstance(subject_name,str) or not subject_name.strip() or self.entity_resolver is None:
                    report.ambiguous += 1
                    report.rejected += 1
                    continue
                try:
                    resolved = self.entity_resolver(tenant_id, subject_name)
                except ValueError:
                    report.ambiguous += 1
                    report.rejected += 1
                    continue
                resolved_id = resolved.get("entity_id") if isinstance(resolved,dict) else None
                if (not isinstance(resolved_id,str) or not resolved_id.strip()
                        or (subject is not None and subject != resolved_id)):
                    report.ambiguous += 1
                    report.rejected += 1
                    continue
                subject = resolved_id
            predicate = candidate.get("predicate")
            value = candidate.get("value")
            policy = getattr(self.store,"predicate_cardinality",None)
            cardinality = policy(tenant_id=tenant_id,predicate=predicate) if (
                callable(policy) and isinstance(predicate,str) and predicate.strip()) else "one"
            asserted_cardinality = candidate.get("cardinality")
            if asserted_cardinality is not None and asserted_cardinality != cardinality:
                report.ambiguous += 1
                report.rejected += 1
                continue
            confidence = candidate.get("confidence", 1.0)
            merge_requested = candidate.get("merge") is True
            if (not isinstance(subject, str) or not subject.strip()
                    or not isinstance(predicate, str) or not predicate.strip()
                    or not isinstance(value, dict)
                    or not isinstance(confidence, (int, float))
                    or isinstance(confidence, bool)
                    or not math.isfinite(confidence)
                    or not 0.0 <= confidence <= 1.0
                    or confidence < self.confidence_threshold):
                report.rejected += 1
                continue
            try:
                canonical = self._canonical(value)
                atomic_apply = getattr(self.store, "apply_candidate", None)
                if callable(atomic_apply):
                    extra = {"merge": merge_requested} if self._atomic_supports_merge else {}
                    if self._atomic_supports_observed and observed_at is not None:
                        extra["observed_at"] = observed_at
                    result = atomic_apply(
                        tenant_id=tenant_id, subject_id=subject, predicate=predicate,
                        value=value, source_id=source_id, confidence=float(confidence),
                        **extra)
                    memory_id, replaces_id, duplicate, relation = self._normalize_atomic(result)
                    if duplicate:
                        report.duplicates += 1
                        report.relations.append({
                            "subject_id": subject, "predicate": predicate,
                            "relation": RELATION_DUPLICATE, "memory_id": memory_id,
                            "merged": False})
                        continue
                else:
                    memory_id, replaces_id, relation = self._apply_fallback(
                        tenant_id=tenant_id, subject=subject, predicate=predicate,
                        value=value, canonical=canonical, source_id=source_id,
                        confidence=float(confidence), merge_requested=merge_requested,
                        observed_at=observed_at)
                    if relation == RELATION_DUPLICATE:
                        report.duplicates += 1
                        report.relations.append({
                            "subject_id": subject, "predicate": predicate,
                            "relation": RELATION_DUPLICATE, "memory_id": memory_id,
                            "merged": False})
                        continue
                report.created.append(memory_id)
                if replaces_id:
                    report.replaced.append(replaces_id)
                merged = relation == RELATION_MERGED
                report.relations.append({
                    "subject_id": subject, "predicate": predicate,
                    "relation": relation, "memory_id": memory_id, "merged": merged})
                if self.embedder is not None:
                    try:
                        model_id, vector = self.embedder(candidate)
                        self.store.store_embedding(
                            tenant_id=tenant_id, memory_id=memory_id,
                            model_id=model_id, vector=vector)
                    except Exception:
                        # The canonical fact and outbox remain committed. A
                        # projection worker can retry vector indexing later.
                        report.embedding_failures.append(memory_id)
            except (TypeError, ValueError):
                report.rejected += 1
        return report

    @staticmethod
    def _normalize_atomic(result):
        """Accept both the legacy 3-tuple and the relation-aware 4-tuple."""
        if len(result) == 4:
            return result
        memory_id, replaces_id, duplicate = result
        relation = RELATION_DUPLICATE if duplicate else RELATION_SUPERSEDES
        return memory_id, replaces_id, duplicate, relation

    def _apply_fallback(self, *, tenant_id: str, subject: str, predicate: str,
                        value: dict, canonical: str, source_id: str,
                        confidence: float, merge_requested: bool, observed_at=None):
        """Non-atomic stores (SQLite reference backend, test doubles)."""
        clock = getattr(self.store, "current_time", None)
        now = observed_at or (clock() if callable(clock) else datetime.now(timezone.utc))
        active = self.store.recall(
            tenant_id=tenant_id, subject_id=subject,
            predicate=predicate, valid_at=now, known_at=now)
        exact = next((row for row in active
                      if self._canonical(row["value"]) == canonical), None)
        if exact is not None:
            link_source = getattr(self.store, "link_claim_source", None)
            if callable(link_source):
                link_source(tenant_id=tenant_id, memory_id=exact["id"],
                            source_id=source_id, confidence=confidence)
            return exact["id"], None, RELATION_DUPLICATE
        policy = getattr(self.store,"predicate_cardinality",None)
        cardinality = policy(tenant_id=tenant_id,predicate=predicate) if callable(policy) else "one"
        if cardinality == "many":
            memory_id=self.store.remember(
                tenant_id=tenant_id,subject_id=subject,predicate=predicate,
                value=value,source_id=source_id,valid_from=now)
            return memory_id,None,RELATION_CREATED
        if len(active)>1:
            raise ValueError("Single-valued predicate has multiple active claims; reconcile first")
        replaces_id = active[0]["id"] if active else None
        relation = RELATION_CREATED if replaces_id is None else RELATION_SUPERSEDES
        payload = value
        if replaces_id is not None and merge_requested:
            relation = classify_relation(value, active[0]["value"])
            if relation == RELATION_MERGED:
                try:
                    merged = deep_merge(active[0]["value"], value)
                except TypeError:
                    merged = None
                if merged is not None:
                    payload = merged
                else:
                    payload = value
                    relation = RELATION_CONTRADICTION
            elif relation == RELATION_CONTRADICTION:
                # Resolve scalar conflicts in favor of the newer candidate
                # while preserving non-conflicting knowledge.
                try:
                    payload = resolve_conflicts(active[0]["value"], value)
                except TypeError:
                    payload = value
        memory_id = self.store.remember(
            tenant_id=tenant_id, subject_id=subject, predicate=predicate,
            value=payload, source_id=source_id, valid_from=now,
            replaces_id=replaces_id)
        return memory_id, replaces_id, relation
