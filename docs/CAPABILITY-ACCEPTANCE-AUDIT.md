# Capability Acceptance Audit — reliable projection candidate

Audit date: 2026-11-09  
Candidate branch: `feat/reliable-projection-engine`  
Base: `main` at `60329aa1`  

Repository: `hippoley/Agent-Memory-Core-v0.1.0`

This replaces earlier audit snapshots that predated the expanded REST facade.
The audit distinguishes implementation, tests, external API exposure and
production readiness. A successful unit test is not described as a real
service integration test.

## Status

| Capability | Implementation found | HTTP API | Evidence / remaining boundary |
|---|---|---|---|
| PostgreSQL canonical memory | Yes | Yes | Claims, bitemporal recall, provenance and transactional event writes; real PostgreSQL integration workflow |
| pgvector | Yes | Yes | 384D/512D embedding write and vector search; caller supplies embeddings; real pgvector integration workflow |
| Apache AGE | Yes | Yes | Canonical entity/edge writes and AGE projection/traversal; real AGE integration workflow |
| Hybrid retrieval | Yes | Yes | SQL + vector + graph channels and degraded-channel reporting; real DB workflow |
| Time-series observations | Yes | Yes | Ingest, event-time range query, count/min/max/avg; real DB integration test; not a full TSDB |
| Local object store | Yes | Yes | Atomic content-addressed writes and checksum-verified reads; local tests and HTTP API tests |
| S3-compatible object store | Yes | Adapter + HTTP wiring | boto3 adapter and server environment configuration; current adapter test uses a fake S3 client, not live AWS/MinIO |
| Durable events | Yes | Yes | PostgreSQL event log, cursor replay, claim/observation events |
| Realtime SSE | Yes | Yes | Authenticated bounded SSE with replay cursor and Last-Event-ID; TestClient contract test; production fan-out/load not measured |
| Transactional outbox | Yes | Internal worker | Bounded exponential retry, attempt timestamps, dead-letter isolation, operator requeue; real PostgreSQL integration tests |
| Per-destination projection fan-out | Yes | Internal worker process | Claim-created events atomically fan out to independent FTS/vector/AGE jobs; per-destination state, retry and DLQ; CLI supports one-shot and supervised polling |
| FTS projection | Yes | `GET /v1/search/lexical` | PostgreSQL tsvector + pg_trgm CJK substring matching; tenant-scoped; real PostgreSQL integration tests |
| Vector projection | Yes, injected embedder | Existing vector search API | Local embedding function is injected; dimension registry and pgvector upsert; synthetic integration embedder verifies wiring, not model quality |
| AGE claim projection | Yes | Existing graph retrieval API | Fixed-label tenant-qualified MemoryClaim nodes plus projection ledger; real PostgreSQL+AGE integration test; existing edge projection remains separate |
| Temporal | Narrow verification workflow | Yes, start/status/cancel | Real local Temporal SDK workflow + PostgreSQL activities; standalone worker command; no general workflow authoring or production cluster operations |
| Mem0-inspired processing | Deterministic first stage | Yes | Structured candidate validation, confidence filtering, atomic per-fact deduplication/replacement, provenance linking, alias resolution and explicit positive/negative assertion polarity; model extractor injected by caller |
| Local-model extraction | Optional loopback adapter | Yes, `POST /v1/memories/extract` | OpenAI-compatible local endpoint only; bounded context, JSON validation, polarity and tenant-local self subject; 12-case smoke benchmark exists but is not release-grade |
| Memory consolidation / forgetting | Not complete | No | Summarization, consolidation scheduling, policy-based forgetting, contradiction classification and benchmark remain |
| Third-party REST contract | Yes, broad prototype | Yes | Swagger/OpenAPI, tenant-scoped lexical route, explicit assertion polarity, Idempotency-Key writes, cursor event replay and dependency-free Python SDK; not yet hardened for untrusted multi-customer public SaaS |

## Current public HTTP endpoints

- `GET /v1/health`
- `GET /v1/ready`
- `GET /v1/capabilities`
- `POST /v1/memories` (optional `Idempotency-Key`, positive/negative polarity)
- `GET /v1/memories/{subject_id}`
- `POST /v1/memories/process`
- `GET /v1/memories/{memory_id}/sources`
- `PUT /v1/memories/{memory_id}/embedding`
- `GET /v1/search/lexical`
- `POST /v1/search`
- `PUT /v1/objects`
- `GET /v1/objects/{digest}`
- `POST /v1/observations`
- `GET /v1/observations/{device_id}`
- `GET /v1/observations/{device_id}/aggregate`
- `POST /v1/entities`
- `GET /v1/entities/resolve`
- `POST /v1/edges`
- `GET /v1/events`
- `GET /v1/events/stream`
- `POST /v1/workflows/memory-verifications`
- `GET /v1/workflows/{workflow_id}`
- `POST /v1/workflows/{workflow_id}/cancel`

## Important limitations and risks

### Object ownership

The service binds one API key to one tenant per process. PostgreSQL now records
`(tenant_id, object_sha256)` ownership on upload; object downloads and memory
claim references both verify that ownership. A shared physical content-addressed
store can therefore be used without granting another tenant read/reference
access through this API. Direct bucket access, lifecycle/deletion, quotas,
malware scanning and backup policy remain deployment concerns; keep buckets
private and do not grant clients direct object-store credentials.

### Realtime semantics

SSE currently polls the durable event log and emits events with IDs. Clients
can reconnect using `Last-Event-ID` or `after`. It is not a broker-based
fan-out service; no multi-replica ordering, high-volume throughput or
backpressure guarantees have been demonstrated.

### Time series

This is a PostgreSQL observation subsystem, not TimescaleDB or another
specialized TSDB. It handles event time vs ingest time, idempotent ingestion,
late arrivals, bounded queries and simple aggregates. Retention, partitioning,
downsampling, continuous aggregates and load benchmarks remain.

### Memory processing

The current API receives structured candidate facts from a caller-supplied
extractor and applies deterministic memory logic. PostgreSQL writes are
serialized per tenant/subject/predicate; positive/negative polarity is stored
explicitly. It does not infer facts from arbitrary raw text itself. Fuzzy
multilingual entity resolution, model-based conflict classification, memory
consolidation, expiration/forgetting policy and Chinese evaluation corpus remain.

### Temporal

The SDK integration demonstrates a durable workflow for verification and
receipt writing. The registered memory-verification workflow supports REST start/status/cancel
and has a standalone worker command. General-purpose workflow authoring,
Signal/Update, arbitrary workflow registration, worker autoscaling and
production Temporal cluster operations are not implemented.

### Idempotency retention

Claim idempotency is tenant-scoped and transactionally bound to the created
claim. The current ledger has no automatic expiry/compaction policy; add a
retention job before high-volume long-lived deployment.

### Security / deployment

The API is single-tenant per process and uses a configured bearer key. Before
public multi-customer deployment, add database RLS, key lifecycle, TLS,
rate limiting, audit events, request quotas, object lifecycle/deletion,
quotas, orphan cleanup and backup/restore exercises.

## CI evidence interpretation

Relevant workflows include unit/API contract, real PostgreSQL+pgvector+AGE,
memory data flow and Temporal+PostgreSQL integration. Always verify that
workflow runs target the exact release candidate SHA. Do not combine green
runs from different commits and call the latest main fully qualified.

## Next engineering gates

1. Add real MinIO integration tests, object lifecycle/deletion, quota and orphan cleanup.
2. Complete a supervised worker lifecycle for per-destination projection jobs,
   including lease/heartbeat policy, graceful shutdown, operational metrics and alerting.
3. Add webhook subscription delivery and replay/reconciliation semantics.
4. Add observation retention/partition policy and sustained ingestion tests.
5. Add local-model extraction adapters, cross-turn Chinese reference resolution,
   contradiction classification and a Chinese candidate extraction evaluation set.
6. Add Temporal signals/updates and generalize workflow registration only after
   worker deployment and lifecycle semantics are stable.
7. Add RLS, API key rotation/revocation, rate limits and audit trails.
8. Run the complete suite against one immutable release candidate SHA and
   publish the resulting test matrix.

## Bottom line

The current repository is materially more complete than earlier audit snapshots:
the public API now includes object upload/download, observation ingestion/query/
aggregation, vector writes, entity/edge writes, durable event replay, SSE and
structured memory processing. PostgreSQL/pgvector/AGE and Temporal have real
integration test paths. The remaining gap is not "all features are missing";
it is production hardening, richer memory intelligence, object lifecycle
controls, worker observability/lease management, webhook delivery and general
Temporal workflow management.
