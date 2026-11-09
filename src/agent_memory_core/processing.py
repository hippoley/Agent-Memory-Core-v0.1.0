"""Model-agnostic memory extraction, deduplication and conflict application.

The model/extractor is deliberately injected: this package does not require a
cloud LLM. The deterministic processing stage validates candidates, deduplicates
identical active facts, versions changed facts, and optionally indexes vectors.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Callable


@dataclass
class ProcessingReport:
    created: list[str] = field(default_factory=list)
    duplicates: int = 0
    rejected: int = 0
    replaced: list[str] = field(default_factory=list)
    embedding_failures: list[str] = field(default_factory=list)


class MemoryProcessor:
    """Apply structured facts emitted by a caller-provided extraction model.

    Extractor contract: extractor(text) returns an iterable of dictionaries.
    Each candidate has subject_id, predicate, value, and optional confidence.
    An optional embedder receives the normalized candidate and returns a
    (model_id, vector) pair.
    """

    def __init__(self, store, *, confidence_threshold: float = 0.65,
                 embedder: Callable | None = None):
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        self.store = store
        self.confidence_threshold = confidence_threshold
        self.embedder = embedder

    @staticmethod
    def _canonical(value: dict) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)

    def process(self, *, tenant_id: str, text: str, source_id: str,
                extractor: Callable[[str], object]) -> ProcessingReport:
        if not tenant_id or not text.strip() or not source_id:
            raise ValueError("tenant_id, non-empty text and source_id are required")
        report = ProcessingReport()
        candidates = extractor(text)
        now = datetime.now(timezone.utc)
        for candidate in candidates:
            if not isinstance(candidate, dict):
                report.rejected += 1
                continue
            subject = candidate.get("subject_id")
            predicate = candidate.get("predicate")
            value = candidate.get("value")
            confidence = candidate.get("confidence", 1.0)
            if (not isinstance(subject, str) or not subject.strip()
                    or not isinstance(predicate, str) or not predicate.strip()
                    or not isinstance(value, dict)
                    or not isinstance(confidence, (int, float))
                    or not 0.0 <= confidence <= 1.0
                    or confidence < self.confidence_threshold):
                report.rejected += 1
                continue
            try:
                canonical = self._canonical(value)
                active = self.store.recall(
                    tenant_id=tenant_id, subject_id=subject,
                    predicate=predicate, valid_at=now, known_at=now)
                exact = next((row for row in active
                              if self._canonical(row["value"]) == canonical), None)
                if exact is not None:
                    report.duplicates += 1
                    continue
                replaces_id = active[0]["id"] if active else None
                memory_id = self.store.remember(
                    tenant_id=tenant_id, subject_id=subject, predicate=predicate,
                    value=value, source_id=source_id, valid_from=now,
                    replaces_id=replaces_id)
                report.created.append(memory_id)
                if replaces_id:
                    report.replaced.append(replaces_id)
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
