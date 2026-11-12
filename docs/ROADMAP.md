# Roadmap · Implementation and verification gates

The repository is a **working local research prototype**, NOT a secure production service.

| Gate | Work | Current evidence | Status |
| --- | --- | --- | --- |
| G0 | SQLite fact/time/object core | Unit tests in `test.yml` | ✅ Prototype verified |
| G1 | PostgreSQL fact tables + pgvector search | Real Postgres + vector write/query in `integration.yml` | ✅ Prototype verified |
| G2 | AGE two-hop traversal, canonical edge projection | Real same-database graph test, idempotent replay | ✅ Prototype verified |
| G3 | SQL + vector + graph hybrid recall | Real three-channel candidate merge and tenant test | ✅ Prototype verified |
| G4 | Bitemporal interval correction, historical evidence semantics | Basic validity/as-of only, full interval revision missing | ◻ Incomplete |
| G5 | Authentication, tenant RLS, encryption, erasure and recovery | Threat model and adversarial database tests absent | ◻ Not started |
| G6 | Graph deletion/reconciliation and rebuilding | Immutable-edge projection only | ◻ Incomplete |
| G7 | Local embeddings, retrieval quality and latency benchmarks | Fixed 384-dim profile, no local encoder or Recall@k | ◻ Incomplete |
| G8 | Real sensor time-series partitioning and object lifecycle | Observation schema and SHA-256 store only | ◻ Incomplete |
| G9 | Temporal / Restate / WindowPilot adapters | Temporal SDK Workflow + real local Worker/Activities + PostgreSQL idempotent receipt CI; Restate/WindowPilot not implemented | 🟡 Temporal prototype verified |
| G11 | Mem0-inspired extraction and multi-signal retrieval | Structured candidate pipeline, confidence filter, exact dedupe, provenance and atomic per-key PostgreSQL processing implemented; real Chinese model evaluation pending | 🟡 Prototype verified |
| G12 | Realtime outbox + change feed + snapshot query | Durable event log, cursor replay, SSE, signed Webhook subscriptions, delivery retry/DLQ/replay and transactional outbox recovery implemented; production fan-out/load pending | 🟡 Prototype verified |
| G13 | Temporal durable orchestration adapter | Optional SDK adapter; real local Temporal dev service CI, no Temporal dependency in core | 🟡 Prototype verified |
| G10 | Interop spec + external reproducer | Versioned MemoryPort + local REST contract, authenticated tenant-scoped API tests; external adopter absent | 🟡 Prototype verified |

## Verified version profile

GitHub Actions real database verified PostgreSQL **18.6**, Apache AGE **1.8.0**,
pgvector **0.8.7**, on 2026-11-09. This is the observed integration profile;
the Dockerfile is based on `apache/age:latest`, so image revisions can change.
For reproducibility, pin base digest and package versions before production.

## Next blocking work

1. Complete graph rebuild and deletion propagation before mutable edge governance.
2. Enforce tenant authorization at the database boundary, not just query wrappers.
3. Implement local embeddings and relevance/latency evaluation on real Chinese agent corpora.
4. Define physical evidence and workflows only through adapters, not memory side effects.

Not planned: replicating Temporal, hosting a multi-model cloud platform or
implementing a new database engine.

## Packaging closure

The authoritative PostgreSQL DDL is mirrored as a package resource so wheels
do not depend on the source checkout path. The unit test asserts byte equality
against the repository SQL and CI checks resource inclusion in built wheels.


## Lightweight architecture decision

See [ADR-0004](ADR-0004-lightweight-memory-platform.md). The design keeps one
mandatory database and zero mandatory sidecar services. PostgreSQL is the
authority; pgvector, AGE and full-text search are in-database indexes. Object
bytes use local content-addressed files by default. Temporal is an optional
durable workflow control plane for long-running/retryable processes, never a
synchronous retrieval dependency. Mem0 contributes extraction, deduplication,
entity-aware and multi-signal retrieval patterns, not a required platform or
a second source of truth.


## Composability and public API gate (2026-11-09)

- `MemoryPort` and capability discovery define a versioned public boundary.
- Local REST routes: health, capability discovery, claim write, claim recall,
  and optional hybrid search. REST-to-real-PostgreSQL write/read has a CI test.
- Hybrid retrieval records vector/AGE degradation and continues with canonical
  SQL; canonical SQL failure remains visible and is not silently hidden.
- Temporal workflow/activities are optional; ordinary memory API does not
  require a Temporal service.
- Latest verified profile: unit, API contract, real Postgres/pgvector/AGE,
  end-to-end data-flow and Temporal integration workflows are green on the
  relevant commits. Re-run all workflows on the same release tag before cutting
  a release; green historical runs on different commits are not one atomic
  release qualification.

Remaining critical gaps:
1. Run a single release-candidate commit through every workflow.
2. Add explicit kill/restart fault injection for AGE, vector, Temporal and object
   backend, with measured recovery and no silent data loss.
3. Add object backend protocol and external S3-compatible implementation.
4. Complete public graph/object/time-series endpoints and SDK contract.
5. Tenant isolation at DB boundary (RLS), key rotation, TLS, rate limiting,
   audit, backup/restore and retention/erasure.
6. Chinese retrieval benchmark: Recall@k, MRR/nDCG, p50/p95 latency, memory,
   CPU and integration overhead on the same corpus.


## RSI implementation delta (2026-11-10)

- Same-key memory processing is now serialized with PostgreSQL transaction
  advisory locks; concurrent duplicate candidates preserve provenance while
  producing one canonical fact and one outbox event.
- Transactional outbox now has bounded retries, exponential backoff, dead-letter
  state and tenant-scoped replay.
- Local Ollama extraction enforces a strict fact schema and marks utterance
  content as untrusted input.
- Added real-service evaluation runners for Chinese extraction and retrieval.
  They intentionally require 200+ human-adjudicated examples and do not report
  success until measured thresholds are met.

These are implementation advances, not a release sign-off. Database RLS,
measured local model quality, Chinese retrieval quality, process/database
fault recovery, and load/performance evidence remain release blockers.



## Trusted predicate cardinality (2026-11-12)

Tenant-scoped `memory_predicate_policies` now defines trusted `one` / `many`
semantics. Missing policy defaults to `one`. Writes and policy changes share
advisory-lock ordering; direct writes enforce non-overlapping single-valued
claims, while multi-valued facts coexist and deduplicate by canonical value.
Model-supplied cardinality cannot override the database policy. Under the
principal-bound RLS mode, tenant roles can read but cannot mutate these policies.

Real PostgreSQL acceptance: `tests/test_predicate_cardinality.py`.
The 100-Agent same-fact benchmark is `scripts/benchmark_memory_concurrency.py`.
