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
3. Serializes concurrent updates for the same tenant/subject/predicate key using a transaction-scoped PostgreSQL advisory lock.
4. Reads current facts for that subject and predicate inside the same transaction.
5. Deduplicates identical active facts using canonical JSON and links the new provenance source atomically.
6. Versions a changed fact using the existing claim correction mechanism.
7. Optionally resolves subject aliases against the tenant-scoped canonical entity registry.
8. Links every supporting source to the canonical claim; repeated facts add provenance rather than losing the new source.
9. Optionally calls an injected embedder and stores the vector.
10. Reports embedding failures separately; the canonical fact is not discarded.

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
        "polarity": "positive",
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

The PostgreSQL backend now has an atomic candidate-application path. The
canonical claim schema stores polarity as a first-class positive/negative
field. A negated candidate with the same predicate and value versions the
previous assertion instead of being mistaken for an exact duplicate. The
extractor must decide polarity; storage validates and preserves it rather than
trying to infer Chinese negation with brittle keyword rules. Concurrent
processors for the same fact key cannot both create duplicate active claims.
Other storage adapters without apply_candidate retain the compatibility
read-then-write fallback and do not inherit this concurrency guarantee.

This is the first deterministic processing stage, not a full Mem0 clone.
The extractor is injected and must be provided by the application. Model-native extraction, richer entity resolution (including fuzzy and cross-lingual
resolution), contradiction classification, consolidation/summarization,
forgetting policies and a Chinese extraction/retrieval benchmark remain to be
implemented and evaluated.


## Built-in loopback local model adapter

The optional `LocalOpenAICompatibleExtractor` calls a local
OpenAI-compatible chat-completions server. It accepts loopback HTTP URLs only
(`localhost`, `127.0.0.1`, or `::1`), disables redirects, bounds response
size, requests JSON output and validates candidate shape/polarity. It does not
call a hosted model.

Configure the API process:

```bash
export MEMORY_LOCAL_MODEL_URL='http://127.0.0.1:1234/v1'
export MEMORY_LOCAL_MODEL='your-local-qwen-model-name'
export MEMORY_LOCAL_MODEL_TIMEOUT='30'
uvicorn agent_memory_core.server:app --host 127.0.0.1 --port 8765
```

Then call `POST /v1/memories/extract` with `text` and `source_id`. The
endpoint is unavailable (503) unless both local model settings are configured.
The model proposes structured facts; it does not write directly to PostgreSQL.
The deterministic processor still validates confidence, positive/negative
polarity, entity resolution, provenance, deduplication and corrections.

The adapter's loopback restriction is intentional. If the model server is
inside a separate container/network namespace, use a local reverse proxy bound
to loopback rather than weakening the default to arbitrary remote hosts.


### Cross-turn context

`POST /v1/memories/extract` may include up to 20 prior `context` turns,
each with role `user` or `assistant` and bounded content. The local adapter
uses them only as context for resolving a reference in the current text.
Assistant turns are explicitly not evidence of user preferences. If the
referent is ambiguous, the extraction prompt instructs the model to omit the
candidate rather than invent an entity. The deterministic processor still
requires a resolvable `subject_id` or registered `subject_name`.


## Evaluation harness

A first 12-case Chinese smoke dataset and a local-model runner now live under
`benchmarks/` and `scripts/evaluate_local_extractor.py`. It measures exact
candidate precision/recall/F1 and polarity accuracy. Reversing a negation is
counted as both a false positive and false negative. These fixtures are a
starting point, not a release-quality benchmark; they still need human
adjudication and broader multi-turn coverage.


### Reserved first-person subject

The local extractor contract reserves `subject_id="self"` for explicit
first-person facts about the authenticated speaker. The tenant boundary makes
that identifier tenant-local in practice; it is not a globally shared person
ID. Other people and physical entities still require a known stable
`subject_id` or a resolvable `subject_name`.
