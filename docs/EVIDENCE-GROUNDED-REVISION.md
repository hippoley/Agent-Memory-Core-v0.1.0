# Evidence-grounded memory revision — cross-industry opt-in

This is the **general** memory-update path. No industry rule pack, fixed list
of subject types, or Chinese vocabulary is required. A configurable model
supplies the semantic interpretation; canonical claims and trusted policies
determine whether the proposed revision can be committed.

## Pipeline

1. POST /v1/memories/process with `ground_with_memory: true`, a current
   utterance and an optional bounded `context_turns` list (at most 3).
2. The server searches **only the authenticated tenant's current canonical
   memories**, using its configured SQL/FTS/graph channels and optional query
   embedding. Maximum 8 evidence claims are passed to the extractor.
3. OpenAI-compatible and local Ollama extractors receive the current text,
   prior turns, and these exact canonical memory records as separate JSON
   data fields. The model proposes domain-neutral UPSERT/RETRACT candidates.
4. An evidence guard rejects a RETRACT unless it cites a retrieved memory ID,
   and its exact subject, predicate and old value match the cited record.
5. The deterministic processor enforces trusted cardinality, confidence,
   valid subject, and database support for the expected-value and claim-ID
   preconditions. PostgreSQL locks the same fact key as upserts, then compares
   ID and JSONB **inside a single atomic transaction** with the audit events.
6. A model may also propose an UPSERT to create a new active value. The
   canonical ledger and outbox retain the old claim and provenance.

## Example: Supplier settlement frequency (no vocabulary pack)

```bash
curl -s http://127.0.0.1:8000/v1/memories/process \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "Supplier-7 now settles quarterly rather than monthly.",
    "source_id": "conversation:turn-2",
    "context_turns": ["Supplier-7 settles monthly."],
    "ground_with_memory": true
  }'
```

The first claim must actually exist in the tenant, and the model must actually
identify it. For example, a RETRACT should carry `expected_memory_id`,
`expected_value` and exact canonical `subject_id` / `predicate`. If it
references a missing/stale/mismatched claim, **the withdrawal is refused**.

If no relevant memory can be retrieved, this path can still create ordinary
UPSERT facts; it may **not** execute a model-proposed RETRACT without evidence.
Run the appropriate embedding model and enable vector search to improve
cross-phrase retrieval; the LLM's ability to infer predicates or amendments
does not imply that search necessarily found the right old fact.

## Important boundaries

- `ground_with_memory` is explicitly opt-in because previously stored facts
  may be sent to the configured external LLM provider. Do not use a hosted
  model for private data unless the operator has authorized that disclosure.
- A model response is a **proposal**, not an authorization or a trusted policy
  update. Model-selected memory references are compared with current canonical
  data and never used alone as deletion permission.
- Search candidates are bounded to the top 8 and may still miss a relevant
  claim. No retrieval hit => no grounded withdrawal. This implementation
  does not yet provide multi-pass semantic planning or a clarification turn.
- Multi-candidate work (RETRACT then UPSERT) is still processed as separate
  MemoryProcessor operations. The **single forget_many batch** is atomic,
  but the entire two-operation revision has **not** been proven all-or-nothing.
  This must be improved before describing generic cross-industry revision as
  a single atomic transaction.
- Tests use fake model responses and real PostgreSQL; no real local LLM quality
  score has been established by this change.
