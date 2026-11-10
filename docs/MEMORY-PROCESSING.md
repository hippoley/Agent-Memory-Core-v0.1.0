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

## Association safety and trusted cardinality (November 2026)

Predicate cardinality is a **tenant-scoped server policy** stored in
`memory_predicate_policies`. Its default is `one` for compatibility.
An unconfigured predicate must not be changed to `many` by an extractor.
The optional candidate `cardinality` field is only a consistency assertion.
If it disagrees with the stored policy the candidate is rejected as ambiguous.

Configure a known multi-value relation from a trusted administrative context,
not from the LLM extractor or public untrusted request body:

```python
db.migrate()
db.set_predicate_cardinality(
    tenant_id="home", predicate="HAS_WINDOW", cardinality="many")
```

Then a model-generated candidate may omit cardinality entirely, or assert
`"cardinality": "many"`:

```python
{
    "subject_id": "living-room",
    "predicate": "HAS_WINDOW",
    "value": {"target_id": "window-b"}
}
```

Different values coexist for a multi-valued predicate, while canonical
duplicates link their source. For a single-valued predicate, a different fact
supersedes the sole active one. Multiple active single-valued claims are
ambiguous and never arbitrarily resolved.

If `subject_name` is supplied and an entity resolver is configured,
a missing, ambiguous, or contradictory resolution is rejected.
`report.ambiguous` is a subset of `report.rejected`.

**Security and reliability boundaries:** Calling
`set_predicate_cardinality` must be limited to an authenticated trusted
administrative control plane. Direct `remember()` writes now enforce overlapping single-value claims
with tenant-scoped transaction advisory locks (including competing first
writers). `add_edge()` validates both graph endpoints against
`memory_entities` for the same tenant, but arbitrary claim payloads such as
`value.target_id` are **not** yet typed or entity-validated.

The public processing API enforces a confidence floor of 0.65 and returns
`ambiguous` separately. The canonical PostgreSQL connector uses autocommit
for standalone reads and explicit transactions for mutations, preventing
implicit read transactions from silently rolling back successful writes on
connection close.

**Still missing:** semantic deduplication for concurrently repeated
multi-valued facts, model-driven relation retractions/negative assertions,
automated data reconciliation, crash/restart load testing, and a true
cross-request idempotent claim command. Policy changes for a predicate with
existing claims are rejected pending an audited migration.
