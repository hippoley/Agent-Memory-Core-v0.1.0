# Architecture review · reality versus diagram

**Scope:** current code on `feat/public-demo-and-architecture-docs`, aligned to the main-line engine. This document is an engineering design review, **not** a release sign-off or independent security audit. Reference diagrams: [runtime topology](assets/architecture.svg) and [memory lifecycle](assets/memory-lifecycle.svg).

## Executive architectural verdict

The fundamental design is coherent for a **single-node, local-first, evidence-aware memory service**:

- PostgreSQL owns canonical claims, histories and event records.
- pgvector and AGE are co-located PostgreSQL extensions, not independent data authorities.
- The extraction provider is replaceable; structured candidates allow basic write/read without deploying an LLM.
- Object bytes and long-running workflow execution are adapters, not requirements for every memory request.
- The hybrid retrieval path rechecks canonical temporal and tenant constraints after collecting indexed candidates.

However, **not every proposed trust property is already enforced**. In particular, authorization and provenance checks should not be represented as a fully implemented general trust gate; resource superiority is not yet measured; and model outputs are not independently verified truths.

## Audit of important edges

| Edge / claim | Concrete source | What is actually true | Design consequence |
|---|---|---|---|
| Browser Console → API | `src/agent_memory_core/api.py`, `resources/dashboard/index.html` | Console is a same-origin browser client of the FastAPI process, **not downstream of other Agent clients** | The diagram shows them side-by-side as entry points |
| API → Processing | `src/agent_memory_core/server.py`, `processing.py`, `api.py` | API can accept supplied candidates, or use a configured extractor; the extractor can be absent | Do not show an LLM as a mandatory service |
| Processing → canonical commit | `postgres.py` `remember` / `apply_candidate` | PostgreSQL transactions and per-key locking protect canonical writes; provenance and outbox are maintained alongside facts | The transaction, not embedding, is the acknowledgement boundary |
| Canonical → pgvector / AGE / FTS | `src/agent_memory_core/projections.py`, `projection_worker.py`, `hybrid.py` | Derived indexes exist; availability depends on enabled channels, embeddings, worker and extension compatibility | Draw indexes **inside one PostgreSQL installation**, with optional delivery path |
| Graph → search | `hybrid.py` | Graph neighborhood candidates are considered only when an anchor entity and graph adapter are supplied; RLS mode currently disables AGE in server wiring | A configured AGE extension is not equivalent to universally available graph recall |
| Candidate fusion → canonical recheck | `hybrid.py` `search_detailed` | Final SQL filters by tenant, `recorded_at`, `superseded_at`, `valid_from`, `valid_to`; scores use reciprocal rank fusion | Claim only canonical **tenant/temporal/supersession recheck**, not universal authorization or source-quality scoring |
| Retrieval response → provenance | `hybrid.py` `MemoryHit`, `sdk.py` `sources` | Search hits carry `source_id`; additional supporting sources are obtainable from separate provenance endpoint | Say 'provenance-addressable', not 'every search result includes all raw evidence' |
| Outbox → workers | `projections.py`, `projection_worker.py` | Durable projection-delivery/worker routines exist; separate worker start is an operational choice; individual channels can remain unconfigured | Do not imply synchronous or guaranteed immediate semantic visibility |
| FastAPI → Temporal | `server.py`, `temporal_workflows.py` where present | Optional workflow adapter; no always-on Temporal requirement for memory API | Draw as an optional external control plane |
| Memory API → multi-tenant enforcement | `server.py` `rls_mode`, `postgres.py` `assert_database_tenant` | Database-principal RLS mode exists, while default local deployment is not production hardened; mode also changes graph availability | Describe exact deployment mode, never claim general hostile-tenant isolation |
| Lite mode → low RAM | `compose.yaml` and [runtime PR #17](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/pull/17) | Lite can disable application channels, but does not yet remove installed AGE/pgvector extension binaries from the DB image | No numerical 'lightweight advantage' without reproducible host data |

## The invariants to protect

1. **One authority:** all state used to assert a fact must trace back to canonical SQL. Embeddings and graph edges may discover candidates but cannot independently assert a new current claim.
2. **Explicit revision:** replacing a single-valued active claim is a versioned change; two overlapping active claims must not be silently accepted for that predicate.
3. **Two kinds of time:** a request's `valid_at` answers effective-world time; `known_at` answers historical knowledge time. Both must be applied to current/historical retrieval.
4. **Idempotent writes:** retries cannot silently multiply a logical fact or its durable event.
5. **Recoverable delivery:** after canonical commit, projection or outbound-delivery failures must be observable and retryable.
6. **Degraded, not fabricated, retrieval:** missing vector, graph or model provider must surface in capability / diagnostics; no claim that all channels ran when only SQL did.
7. **No false trust leap:** `source_id` and stored provenance are evidence identifiers, not proofs that a source was honest or a fact correct.
8. **No false security leap:** tenant filters and bearer keys do not equal fully audited zero-trust authorization on all deployments.

## Three concrete design risks

### R1 · Security and AGE interaction

`server.py` initializes AGE only if the graph flag is enabled **and** RLS mode is not enabled. This avoids pretending an unverified graph traversal inherits SQL tenant isolation; it also means the architecture diagram cannot advertise RLS-hardened graph queries as finished. A production-grade solution needs an explicit graph projection and query security design with dedicated adversarial tests.

### R2 · Lexical recall is not Chinese semantic correctness

The existing full-text projection calls `to_tsvector('simple', ...)`. This supports certain exact-term paths but is not a demonstrated Chinese tokenization/ranking strategy. Use the 200+ judged Chinese query set and compare exact SQL, FTS, vector and combined retrieval separately before claiming strong Chinese-language memory recall.

### R3 · Lightweight is not equivalent to “database extensions removed”

The Lite profile in PR #17 switches retrieval capabilities off while keeping the combined PostgreSQL image and migration schema. The valid claim is **fewer mandatory independently managed services**. A measured RAM/CPU/storage claim requires a fixed host, image SHA, dataset, concurrency, metrics and baseline; extension-free mode requires an explicit migration plan.

## What these two diagrams intentionally omit

- They do not promise the engine automatically establishes the truth of language-model output.
- They do not show individual microservices for every logical module.
- They do not claim every claim's authorization or source trust is independently proved.
- They do not show Temporal as a canonical record system or a requirement for recall.
- They do not show vector and AGE as separate database installations.
- They do not show every route or every background worker; the diagrams are architectural boundaries, not a full runtime dependency graph.

## Next acceptance gates

| Claim that would be attractive in a pitch | Evidence required before saying it |
|---|---|
| “Runs in 500 MB RAM” (example only, **not a measurement**) | Real combined DB+API+worker sample, peak and steady-state metrics on specified hardware and dataset |
| “Recall is more accurate than vector-only” | Same judged Chinese corpus, fixed encoders, identical candidates, Recall@5/MRR@10 plus statistical uncertainty |
| “Every memory result is authorized” | Adversarial RLS / object / graph / index isolation tests and auth policy coverage |
| “A failed worker never loses a fact” | Crash-after-commit, duplicate-delivery, backlog replay, service restart and durable-restore test evidence |
| “Agents autonomously revise beliefs correctly” | Real model extraction on multi-turn contradiction, negation, correction and temporal cases with gold labels |

## Technical judgment

**Reasonable for a focused research / on-device product:** yes, provided the project remains about *revisioned, provenance-bearing facts*, not a generic automated epistemic reasoning engine. The biggest risk is not PostgreSQL: it is the gap between the ambitious product words “trust” and “belief” and what can actually be measured and enforced at API, model and tenant boundaries.

The next differentiated result should therefore be a **paired vector-only versus canonical-revision evaluation** under real corrections, supplemented by the small-host profile measurements. A polished diagram without these receipts would be marketing, not proof.


## Component necessity — do we really need each one?

| Component | Keep / defer / conditional | Why this is the right boundary now | When to reconsider |
|---|---|---|---|
| PostgreSQL canonical facts | **Keep; mandatory** | ACID revision, SQL predicates, history and outbox all share one transaction boundary | Only after independent evidence that a different store improves correctness/operations |
| PostgreSQL FTS | **Keep, but qualify** | Cheap literal candidate path inside the same database | Chinese retrieval quality must be benchmarked; 'simple' configuration is not segmentation |
| pgvector | **Conditional, usually standard** | Semantics can complement exact SQL when an embedding profile exists | If model latency/cost or corpus size makes exact search sufficient, Lite can disable this channel |
| Apache AGE | **Conditional, never a truth source** | Relationship traversal may help room-device-context use cases | Require graph-vs-SQL adjacency ablation, rebuild tests and RLS-compatible security before claiming it is essential |
| Separate graph database | **Do not add now** | Would duplicate state/operations and introduce distributed consistency work | Only after measured graph workload dominates and AGE/SQL are inadequate |
| PostgreSQL Outbox | **Keep** | Aligns event durability with the canonical transaction | Consider an external broker only when fan-out, throughput or multi-node delivery is demonstrated to require it |
| Temporal | **Optional, narrow use cases** | Needed for durable waiting, retries and long-lived execution; not ordinary memory queries | Add only when a real product workflow needs those semantics |
| S3 / MinIO | **Optional** | Local content-addressed object storage serves a single-node profile | Multi-host access, replicated durability, lifecycle and disaster recovery justify it |
| Mandatory LLM process | **Do not add** | Structured writes and deterministic processing must work without a model | Offer optional local/remote inference adapters for text extraction and embeddings |

### A tempting but incorrect optimization

Removing AGE and pgvector from **runtime calls** does not automatically remove their installed extensions or schema footprint. True resource-minimum profiling may require distinct migration schemes and a separately tested PostgreSQL image. Until those exist, the precise claim is **reduced service count / optional query channels**.

### A tempting but incorrect feature promise

Calling our output a *belief* is a deliberate product metaphor. It must **not** imply probabilistic Bayesian belief updates, world-state verification, autonomous cross-source truth adjudication, or safe automatic execution of physical actions. At present it is a **versioned structured claim with provenance and explicit validity semantics**. Anything more requires a separately specified algorithm and evaluation.
