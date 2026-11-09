# Capability-by-capability acceptance audit

Audit date: 2026-11-09  
Repository: https://github.com/hippoley/Agent-Memory-Core-v0.1.0  
Scope: inspect source, schema, tests and GitHub Actions. This is a code-evidence
audit, not a production security certification or an independent penetration test.

## Status vocabulary

- **Implemented + integration-tested**: real adapter code and a test against
  the real backing service exist.
- **Implemented + unit-tested**: real local code exists, but the test does not
  exercise the production backing service or a remote deployment.
- **Prototype only**: narrow demonstration exists; not a general-purpose feature.
- **Designed only / missing**: docs or schema references exist, but no usable
  implementation of the promised capability.
- **Not exposed**: backend code may exist, but third parties cannot call it via
  the HTTP API.

## Results

| Capability | Code evidence | Test evidence | Verdict |
|---|---|---|---|
| PostgreSQL canonical claims + bitemporal correction | `postgres.py`, `001_core.sql` | `test_pg_runtime.py`, real PostgreSQL CI | Implemented + integration-tested |
| pgvector write/search, 384D and 512D | `store_embedding`, `vector_search`, vector tables | `test_pg_runtime.py`, real pgvector CI | Implemented + integration-tested; REST ingestion absent |
| Apache AGE graph projection/traversal | `age_graph.py`, canonical `memory_edges` | `test_pg_runtime.py`, real AGE CI | Implemented + integration-tested; public graph CRUD absent |
| Time-series observations | `record_observation`, `observations`, `memory_observations`, authenticated REST write/query | `test_pg_runtime.py`, `test_api_objects_observations.py` | Implemented + integration-tested at basic event/observation level; not a full TSDB |
| Local content-addressed object storage | `objects.py`, SHA-256 path, atomic replace, read checksum | `test_core.py::test_object_integrity` | Implemented + unit-tested; no S3 adapter, metadata lifecycle, API upload/download or object-store integration test |
| Event append in SQLite reference backend | `MemoryStore.append_event/events` | `test_core.py::test_event_idempotency` | Implemented + unit-tested; SQLite only |
| PostgreSQL transactional outbox consumer | `outbox.py`, `memory_outbox` | `test_outbox_runtime.py` real PostgreSQL tests | Implemented + integration-tested for DB-local handler, retry and atomic ack |
| Realtime event subscription/stream | PostgreSQL claim/observation event writes, cursor replay API, bounded SSE with Last-Event-ID | `test_api_objects_observations.py` verifies replay and SSE framing; `test_pg_runtime.py` verifies DB event persistence | Implemented + integration-tested at prototype scale; no broker fan-out, DLQ or load test |
| Temporal durable workflow | `temporal_adapter.py` one VerifyMemoryWorkflow + two Activities | `test_temporal_integration.py` starts local Temporal dev service and real PostgreSQL | Prototype only, integration-tested; not general orchestration platform or HTTP API |
| Mem0-inspired processing | `processing.py`: pluggable extraction adapter, confidence gate, canonical dedup, conflict versioning, optional embedding projection | `test_processing.py` plus real PostgreSQL exercise in `test_pg_runtime.py` | First deterministic processing pipeline implemented; model-native extraction, entity resolution, consolidation and Chinese benchmark remain absent |
| Public REST API | `api.py`: health, ready, capabilities, claim write/read, hybrid search, object upload/download, observations, event replay and SSE | `test_openapi_contract.py`, `test_api_postgres_runtime.py`, `test_api_objects_observations.py` | Expanded authenticated REST facade; graph/vector write/admin and workflow APIs still absent |
| Third-party multi-tenant production boundary | single configured tenant per API process; app-level key check | API tenant-scope tests | Single-tenant prototype only; RLS and production identity/security absent |

## Important interpretations

### Time-series

This is not yet a dedicated time-series database or a mature telemetry subsystem.
It is a PostgreSQL observation table with event time and ingestion time,
tenant/device/event identity, idempotent insertion, and ordered retrieval.
The current integration test demonstrates late-arriving events are ordered by
observed time and conflicting event-ID reuse is rejected. Missing: range/
aggregation APIs, retention/partition automation, downsampling, high-ingest
load tests, backpressure and public HTTP ingestion/query routes.

### Object storage

The current implementation is a local filesystem object store addressed by
SHA-256, with atomic file placement and checksum verification. It is not S3,
MinIO or a distributed object service. Missing: S3-compatible adapter, metadata
and authorization, upload/download API, quotas, lifecycle, backup/restore and
orphan reconciliation. The unit test proves local integrity only.

### Events and realtime

There are three distinct things and they must not be conflated:
1. SQLite reference-backend event append/read with idempotency.
2. PostgreSQL transactional outbox with a DB-local consumer, retry and atomic
   acknowledgement.
3. Real-time external subscriptions, replay cursors and reconnect semantics.

Only the first two have code/tests. The third is not implemented. PostgreSQL
NOTIFY is discussed as a wake-up hint in the architecture, but no live
subscriber/stream API was found.

### Temporal

The current workflow verifies one existing memory claim and writes an
idempotent verification receipt. CI starts a local Temporal test service and
runs real PostgreSQL activities. This proves the SDK integration path is real;
it does not prove general-purpose workflow lifecycle, cancellation, signals,
long-running orchestration, production cluster operations or an external API.

### Mem0

The repository's ADR explicitly says it adopts Mem0-inspired algorithmic
patterns rather than depending on Mem0 runtime. However, code inspection finds
no implemented extraction pipeline, deduplication engine, entity resolver,
memory conflict policy, consolidation scheduler or benchmark proving those
features. These must be built and tested before claiming Mem0's core processing
capabilities have been incorporated.

## CI evidence

The repository's Actions history contains successful runs for:
- `test`, `public-api-contract`, `triple-store-integration`,
  `memory-data-flow`, and `temporal-memory-integration` on earlier commits.

These runs occurred on different commits. They are not an atomic release
qualification of the current main HEAD. Re-run every relevant workflow on the
same release candidate SHA before calling the whole release green.

## Acceptance sequence

1. Finish Mem0-inspired processing pipeline with tests and a Chinese benchmark.
2. Add object backend protocol and S3-compatible implementation + API.
3. Add public observation APIs and TS range/aggregation/retention tests.
4. Split outbox into independently tracked projection tasks (vector, FTS, AGE).
5. Add live event consumer API: durable cursor, replay, reconnect, auth and
   duplicate delivery semantics.
6. Generalize Temporal workflows and expose authenticated start/status/signal/
   cancel endpoints; test process restarts and retry behavior.
7. Add graph and vector write/admin endpoints and prove third-party-only
   integration without direct database access.
8. Add RLS, tenant credential lifecycle, TLS, audit, rate limiting and
   backup/restore acceptance.
9. Run the full suite on one release candidate SHA and publish measured
   performance and retrieval-quality results.

## Bottom line

The repository is not empty and these are not all missing: PostgreSQL,
pgvector, Apache AGE, basic time-series observations, local object integrity,
PostgreSQL outbox, and one real Temporal workflow have meaningful code and
tests. But object storage is local-only; realtime streaming and Mem0-style
processing are not implemented; Temporal is a narrow prototype; and most
advanced modules are not yet exposed through the REST API. The product is
currently a tested backend prototype plus a limited authenticated REST
facade—not yet a complete third-party Agent memory/knowledge platform.
