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
6. Optionally calls an injected embedder and stores the vector.
7. Reports embedding failures separately; the canonical fact is not discarded.

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
The extractor is injected and must be provided by the application. Entity
resolution, source-to-claim evidence many-to-many linking, contradiction
classification, consolidation/summarization, forgetting policies and a
Chinese extraction/retrieval benchmark remain to be implemented and evaluated.
