# Memory Processing Pipeline

The core now provides a model-agnostic deterministic processing stage. It does
not call a hosted LLM and does not pretend to extract facts from arbitrary text
without an extractor. The caller supplies a local model, rules engine, or other
extractor that returns structured candidates.

## Contract

Each candidate is a dictionary:

```python
{
    "subject_id": "bedroom-1",
    "predicate": "night_preference",
    "value": {"noise": "quiet"},
    "confidence": 0.96
}
```

The processor:
1. Validates subject, predicate, structured value and confidence.
2. Rejects candidates below the configured confidence threshold.
3. Reads current facts for that subject and predicate.
4. Deduplicates identical active facts using canonical JSON.
5. Versions a changed fact using the existing claim correction mechanism.
6. Optionally resolves subject aliases against the tenant-scoped canonical entity registry.
7. Links every supporting source to the canonical claim; repeated facts add provenance rather than losing the new source.
8. Optionally calls an injected embedder and stores the vector.
9. Reports embedding failures separately; the canonical fact is not discarded.

## Example

```python
from datetime import datetime, timezone
from agent_memory_core.postgres import PostgresMemory
from agent_memory_core.processing import MemoryProcessor

db = PostgresMemory("postgresql://...")
processor = MemoryProcessor(db, confidence_threshold=0.7)

def local_extractor(text):
    # Replace with your local Qwen / rules / structured extraction adapter.
    return [{
        "subject_id": "bedroom-1",
        "predicate": "night_preference",
        "value": {"noise": "quiet"},
        "confidence": 0.96,
    }]

try:
    report = processor.process(
        tenant_id="home",
        text="I prefer a quiet bedroom at night.",
        source_id="conversation:session-1",
        extractor=local_extractor,
    )
    print(report.created, report.duplicates, report.replaced, report.rejected)
finally:
    db.close()
```

## Current boundary

This is the first deterministic processing stage, not a full Mem0 clone.
The extractor is injected and must be provided by the application. Model-native extraction, richer entity resolution (including fuzzy and cross-lingual
resolution), contradiction classification, consolidation/summarization,
forgetting policies and a Chinese extraction/retrieval benchmark remain to be
implemented and evaluated.


## Trusted predicate cardinality

Each tenant can configure a predicate as `one` or `many` before writing facts.
Absent policies default to `one`. The policy is stored in
`memory_predicate_policies` and is read by the atomic candidate path and the
direct PostgreSQL write path.

- `one`: overlapping active facts for the same tenant, subject and predicate
  are rejected unless the write explicitly replaces the only overlapping fact.
  If historical data contains multiple active facts, automated processing fails
  closed rather than choosing an arbitrary row.
- `many`: distinct values coexist; exact duplicates add provenance without
  creating another canonical claim or outbox event.
- Model output cannot set policy. A candidate-supplied cardinality that
  disagrees with the trusted policy is rejected.
- Policy changes are serialized with writes and are blocked once claims for
  that tenant/predicate exist. Reconciliation must happen before changing
  existing history.
- Under principal-bound RLS, tenant logins can read their policy but cannot
  insert, update or delete it. Provision policy through an administrative
  connection before normal API ingestion.

Example:

```python
db.set_predicate_cardinality(
    tenant_id="home-a", predicate="HAS_WINDOW", cardinality="many"
)
```

Do not use `many` merely because an extractor emits multiple candidates.
It is a domain rule: for example, `HAS_WINDOW` may be many, while a
single-valued current setting should remain one.
