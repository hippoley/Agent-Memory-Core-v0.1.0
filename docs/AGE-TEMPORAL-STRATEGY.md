# AGE + Temporal are first-class extension paths — not accidental dependencies

**Status:** roadmap and implementation truth, based on `main` as inspected 2026-11-16. This is not a claim that the extended architecture has passed production validation.

## Why these two belong in a lightweight memory framework

A model can extract a plausible fact. A vector embedding can find a similar fact. Neither explains the *relationship between changing facts* or guarantees that a long-running verification finishes after a process crash.

- **Apache AGE**: temporal graph of entities and relationships. It gives the retrieval engine another *candidate-discovery* dimension for multi-hop questions, not a second source of truth.
- **Temporal**: recoverable processing when validation, reindexing, evidence refresh or review runs across minutes/hours and external systems.
- **PostgreSQL**: the single authority for current canonical claims, bitemporal history, provenance, permissions and atomic mutation.

These are **first-class product concepts** but not mandatory daemon dependencies for every write/query. There should be no need to boot a workflow server merely to remember one fact, nor to traverse a graph to answer a direct exact query.

## Current verified implementation boundary

| Topic | Already exists in repository | Not yet proven |
| --- | --- | --- |
| AGE graph | AGE schema/projections, graph query channel, hybrid candidate fusion, integration tests | Graph-derived facts always track bitemporal revisions under concurrency; secure AGE query with principal-bound RLS |
| Temporal | Optional `temporalio` dependency, `VerifyMemoryWorkflow`, `verify_memory_record`, `write_memory_verification_receipt`, integration scenario using local Temporal environment and PostgreSQL | General-purpose document ingestion, batch consolidation, multi-step human review or automatic model re-verification pipeline |
| pgvector | Schema, embedding adapters, semantic retrieval and tests | Cross-domain retrieval quality with a pinned real embedding model |
| Provenance | Source IDs, supporting sources, bitemporal canonical facts, outbox | External evidence truth, universally strong authorization |
| Revision | Evidence-grounded candidate proposals and compare-and-withdraw on canonical memory ID and old JSON | The entire `RETRACT + UPSERT` compound update is one atomic transaction |

Important: `lite`, `standard`, `full` are real local deployment profiles. Today **all three use PostgreSQL**. Standard/Full enable vector and AGE application flags; Full additionally starts a projection worker. **None automatically start Temporal** or a local language model.

## One visible end-to-end story we should make real

A maintenance agent learns:

1. `pump-17` belongs to `plant-A` and uses `inspection_period = 30 days`.
2. A signed maintenance bulletin updates that interval to 45 days effective next month.
3. The next agent must correctly identify the affected asset, the valid date, relevant source, and the old/new state.
4. A reindex/reverification workflow may crash after canonical commit; on restart the graph/vector views and verification receipt must converge to the committed claim.
5. A separate tenant cannot discover the asset through SQL, AGE traversal or cached context.

The core never needs a 'pump' vocabulary. Entity/relation interpretation comes from an OpenAI-compatible model; retrieval uses SQL, vector and AGE; deterministic constraints decide whether a revision is admissible. Temporal schedules long-running checks, while PostgreSQL owns the result.

### Required acceptance matrix for the story

| Test | Required proof |
| --- | --- |
| Current fact | One canonical active inspection interval with evidence |
| Temporal/bitemporal | Historical `valid_at` and `known_at` give the correct versions |
| Graph projection | Graph edges representing superseded relations are not returned as current |
| Semantic retrieval | New question wording retrieves the correct claim; no alias/tenant leakage |
| Transaction | Force crash during replacement; neither half-updated state can commit |
| Temporal restart | Kill worker during verification and resume; one logical receipt, no duplicate effects |
| Missing provider | Model/embedding/AGE unavailable => explicit degraded results, never invented confidence |
| Scope/RLS | Cross-tenant retrieval denied in all eligible channels; otherwise disable unsafe channel |
| Cost | Measured P50/P95, CPU/RAM, storage and model-call counts on same hardware/data |

The acceptance matrix is a **proposed verification target**, not a checklist already passed.

## Implementation decision record

### Core: bounded and synchronous

- Extraction proposal may call LLM before SQL transaction.
- Memory candidate resolution is constrained by scope, provenance, time and permission.
- Commit old/new claim transition and event/outbox in **one DB transaction**.
- Return canonical commit result without waiting for vector, graph or Temporal.
- Derived views can be rebuilt from canonical events.

### Graph: event-driven projection

Represent `entity_id`, `predicate`, and revision/scope lineage as stable references. Record edges with `valid_from`, `valid_to`, `recorded_at`, `superseded_at`, and evidence ID where relevant. AGE query candidates must be checked against current canonical PostgreSQL facts.

Graph commits are **not** the canonical transaction acknowledgment; any reindex failure is retryable and observable. Keep traversal bounded and be explicit about AGE disabled when principal-bound RLS requirements cannot be met.

### Durable processing: policy-triggered Temporal

Only launch durable workflows for operations whose runtime crosses a failure/retry/waiting boundary:
- batch source extraction / migration
- long-running evidence verification
- periodic revalidation of high-impact facts
- projection reconciliation / reindex
- human-approval checkpoints for destructive or regulated changes

Use stable workflow IDs and idempotent Activities; PostgreSQL remains the state of record. `Temporal Retry` does not imply exactly-once business side effects.

## Adoption rather than architecture theater

First-class AGE/Temporal means maintainers ship **reproducible usage paths**, not merely list them as buzzwords. The README and project roadmap should expose:

- A precise example plus versioned facts and provenance.
- An isolated integration test for graph + current-state recall.
- A Temporal workflow and local activity test with forced retry.
- A table separating real/partial/target capabilities.
- Honest deployment and model requirements.

If these are missing, a GitHub reader should be able to identify the gap and contribute a test, rather than be misled about production readiness.

## How to contribute

See `tests/test_age_projection.py`, `tests/test_temporal_integration.py`, `src/agent_memory_core/age_graph.py`, `src/agent_memory_core/temporal_adapter.py`, and `docs/USER-STORY-ACCEPTANCE.md`.

A strong PR includes a failing User Story, a minimum reproducible test, the exact PostgreSQL/AGE/Temporal versions if applicable, a measured before/after result, and no unsupported general accuracy or security claim.
