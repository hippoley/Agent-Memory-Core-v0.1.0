# Capability Acceptance Audit — refreshed against current main

Audit date: 2026-11-10  
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
| Transactional outbox | Yes | Internal worker | PostgreSQL row locking, atomic DB-local handler acknowledgement and retry; real PostgreSQL integration tests |
| Temporal | Narrow verification workflow | Yes, start/status/cancel | Real local Temporal SDK workflow + PostgreSQL activities; standalone worker command; no general workflow authoring or production cluster operations |
| Mem0-inspired processing | Deterministic first stage | Yes | Structured candidate validation, confidence filtering, exact deduplication, provenance linking, alias resolution and fact replacement; model extractor injected by caller |
| Automatic LLM extraction | No built-in model | No | Local model/rules extractor contract is injected; core does not call a hosted model |
| Memory consolidation / forgetting | Not complete | No | Summarization, consolidation scheduling, policy-based forgetting, contradiction classification and benchmark remain |
| Third-party REST contract | Yes, broad prototype | Yes | Swagger/OpenAPI and route contract tests; not yet hardened for untrusted multi-customer public SaaS |

## Current public HTTP endpoints

- `GET /v1/health`
- `GET /v1/ready`
- `GET /v1/capabilities`
- `POST /v1/memories`
- `GET /v1/memories/{subject_id}`
- `POST /v1/memories/process`
- `GET /v1/memories/{memory_id}/sources`
- `PUT /v1/memories/{memory_id}/embedding`
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
extractor and applies deterministic memory logic. It does not infer facts from
arbitrary raw text itself. Fuzzy multilingual entity resolution, conflict
classification, memory consolidation, expiration/forgetting policy and
Chinese evaluation corpus remain.

### Temporal

The SDK integration demonstrates a durable workflow for verification and
receipt writing. The registered memory-verification workflow supports REST start/status/cancel
and has a standalone worker command. General-purpose workflow authoring,
Signal/Update, arbitrary workflow registration, worker autoscaling and
production Temporal cluster operations are not implemented.

### Security / deployment

The API is single-tenant per process and uses a configured bearer key. Before
public multi-customer deployment, add database RLS, key lifecycle, TLS,
rate limiting, audit events, request quotas, object lifecycle/deletion,
quotas, orphan cleanup and backup/restore exercises.


## RSI re-audit delta (2026-11-10)

The following implementation changes are present on the user-story closure
branch and are not yet release-qualified until its consolidated test run
passes:

- Local Ollama extraction now treats the user utterance as a JSON-encoded
  untrusted data field and rejects malformed fact fields, invalid predicates,
  non-object values, non-finite confidence and out-of-range confidence.
- PostgreSQL candidate application now serializes same-tenant/same-subject/
  same-predicate writes with a transaction advisory lock. Exact duplicates
  add provenance without generating another canonical claim or outbox event.
  A 20-connection real-PostgreSQL race test was added.
- Transactional outbox events now have bounded retry budgets, exponential
  backoff, an explicit dead-letter timestamp and tenant-scoped operator replay.
  Handler writes and acknowledgements remain in one transaction; injected
  failures roll back handler side effects.
- Added opt-in, real-service evaluators for 200+ judged Chinese extraction
  cases and 200+ HTTP retrieval queries. These are measurement harnesses,
  not passing quality results; no human-adjudicated corpus or live local model
  result is committed yet.

### Current hard blockers (not to be marked Done)

1. **S02/S04:** no measured 200-case real local-model quality result. The
   deterministic processor still cannot independently infer negation,
   coreference or semantic corrections; that requires the real model and
   gold-set evaluation.
2. **S06:** no 200-query Chinese retrieval gold set or measured Recall@5/MRR@10.
   The current FTS dictionary is `simple`, not a Chinese segmenter.
3. **S09:** projection and transactional outbox retry/DLQ paths exist, but
   process-kill, database restart, backlog drain and backup/restore drills
   have not been demonstrated.
4. **S10:** database-enforced RLS is not implemented. Current API tenant
   scoping is application-level; do not represent it as hostile multi-tenant
   isolation.
5. **Release performance:** the 100-Agent HTTP benchmark exists, but no
   measured 10,000-round result is recorded on an isolated deployment.
6. **Object lifecycle:** live MinIO/S3, delete/retention, quota and orphan
   reconciliation remain incomplete.

## CI evidence interpretation

Relevant workflows include unit/API contract, real PostgreSQL+pgvector+AGE,
memory data flow and Temporal+PostgreSQL integration. Always verify that
workflow runs target the exact release candidate SHA. Do not combine green
runs from different commits and call the latest main fully qualified.

## Next engineering gates

1. Add real MinIO integration tests, object lifecycle/deletion, quota and orphan cleanup.
2. Add outbox per-channel tasks for vector, FTS and AGE projection, plus retry
   exhaustion and dead-letter handling.
3. Add observation retention/partition policy and sustained ingestion tests.
4. Add processing concurrency/idempotency controls and a Chinese candidate
   extraction evaluation set.
5. Add Temporal signals/updates and generalize workflow registration only after
   worker deployment and lifecycle semantics are stable.
6. Add RLS, API key rotation/revocation, rate limits and audit trails.
7. Run the complete suite against one immutable release candidate SHA and
   publish the resulting test matrix.

## Bottom line

The current repository is materially more complete than earlier audit snapshots:
the public API now includes object upload/download, observation ingestion/query/
aggregation, vector writes, entity/edge writes, durable event replay, SSE and
structured memory processing. PostgreSQL/pgvector/AGE and Temporal have real
integration test paths. The remaining gap is not "all features are missing";
it is production hardening, richer memory intelligence, object lifecycle
controls, projection orchestration and general Temporal workflow management.
