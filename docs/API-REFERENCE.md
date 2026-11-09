# Agent Memory Core — API Reference and Third-party Integration

Contract version: `1.0.0`  
Runtime: FastAPI / OpenAPI 3.x  
Deployment model: local-first, one tenant per API process

This document is the source-oriented guide for the current public HTTP facade.
The generated contract is available at `/openapi.json` and Swagger UI at
`/docs`. Optional channels must be checked through `GET /v1/capabilities`.

## 1. Runtime documentation

When the service is running:

- Swagger UI: `http://127.0.0.1:8765/docs`
- ReDoc: `http://127.0.0.1:8765/redoc`
- OpenAPI JSON: `http://127.0.0.1:8765/openapi.json`

Swagger's Authorize control accepts the API key without the `Bearer ` prefix.

## 2. Local installation

Requirements: Docker Compose and Python 3.10+.

```bash
git clone https://github.com/hippoley/Agent-Memory-Core-v0.1.0.git
cd Agent-Memory-Core-v0.1.0
docker compose up -d --build --wait
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,postgres,api,s3]'
```

Initialize PostgreSQL:

```bash
export MEMORY_DSN='postgresql://memory:local-dev-only@127.0.0.1:55432/memory'
python -c "import os; from agent_memory_core.postgres import PostgresMemory; db=PostgresMemory(os.environ['MEMORY_DSN']); db.migrate(); db.close()"
```

Configure and start the API:

```bash
export MEMORY_API_KEY='replace-with-a-random-secret-at-least-24-characters'
export MEMORY_TENANT_ID='demo-home'
export MEMORY_ENABLE_VECTOR='1'
export MEMORY_ENABLE_AGE='1'
export MEMORY_OBJECT_BACKEND='local'
export MEMORY_OBJECT_ROOT='./data/objects'
uvicorn agent_memory_core.server:app --host 127.0.0.1 --port 8765
```

Windows PowerShell uses `$env:NAME='value'` instead of `export NAME=value`.

The example database password is development-only. Do not expose this
configuration directly to the public internet.

## 3. Authentication and tenant boundary

All endpoints except `GET /v1/health` require:

```http
Authorization: Bearer <MEMORY_API_KEY>
```

One process binds one API key to one server-configured `MEMORY_TENANT_ID`.
Tenant IDs are never accepted from request payloads. This is not yet a
multi-tenant identity provider: deploy separate credentials and isolated
database/object-store boundaries for mutually untrusted customers. Production
RLS, key rotation/revocation, TLS, rate limiting and audit controls remain
deployment acceptance requirements.

## 4. Complete HTTP route inventory

| Method | Path | Capability |
|---|---|---|
| GET | `/v1/health` | Liveness |
| GET | `/v1/ready` | PostgreSQL readiness |
| GET | `/v1/capabilities` | Runtime capability discovery |
| POST | `/v1/memories` | Create canonical bitemporal claim |
| GET | `/v1/memories/{subject_id}` | Recall facts for a subject |
| POST | `/v1/memories/process` | Validate, deduplicate, version and provenance-link extracted candidates |
| GET | `/v1/memories/{memory_id}/sources` | Read claim provenance |
| PUT | `/v1/memories/{memory_id}/embedding` | Attach caller-generated vector |
| POST | `/v1/search` | SQL + optional vector + optional graph retrieval |
| PUT | `/v1/objects` | Upload raw evidence object |
| GET | `/v1/objects/{digest}` | Download and verify evidence object |
| POST | `/v1/observations` | Ingest idempotent device observation |
| GET | `/v1/observations/{device_id}` | Query event-time observations |
| GET | `/v1/observations/{device_id}/aggregate` | Aggregate a time range |
| POST | `/v1/entities` | Upsert canonical entity |
| GET | `/v1/entities/resolve` | Resolve canonical name or alias |
| POST | `/v1/edges` | Create relation and optionally project into AGE |
| GET | `/v1/events` | Durable event replay with cursor pagination |
| GET | `/v1/events/stream` | Bounded SSE stream with reconnect cursor |

## 5. Core memory operations

### 5.1 Create a fact

```bash
curl -X POST http://127.0.0.1:8765/v1/memories \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "subject_id":"bedroom-1",
    "predicate":"night_preference",
    "value":{"noise":"quiet","temperature_c":23},
    "source_id":"conversation:session-001",
    "valid_from":"2026-11-09T00:00:00+08:00"
  }'
```

The response contains the canonical claim UUID. `valid_from` and `valid_to`
are real-world validity intervals; `recorded_at` is when the system learned
the fact. Use `replaces_id` to explicitly correct an active claim with the
same subject and predicate.

### 5.2 Recall with bitemporal semantics

```bash
curl -G http://127.0.0.1:8765/v1/memories/bedroom-1 \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  --data-urlencode 'predicate=night_preference' \
  --data-urlencode 'valid_at=2026-11-01T20:00:00+08:00' \
  --data-urlencode 'known_at=2026-11-05T12:00:00+08:00'
```

This asks: "What did the system know at `known_at` about the world at
`valid_at`?" Timestamps must include a timezone.

### 5.3 Process structured extraction candidates

The core deliberately does not call a hosted model. Your local Qwen model,
rules engine or other extractor produces candidate facts; the API performs
deterministic validation, confidence filtering, exact-fact deduplication,
entity alias resolution, source linking and version replacement.

```bash
curl -X POST http://127.0.0.1:8765/v1/memories/process \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "text":"I prefer a quiet bedroom at night.",
    "source_id":"conversation:session-002",
    "confidence_threshold":0.7,
    "candidates":[{
      "subject_id":"bedroom-1",
      "predicate":"night_preference",
      "value":{"noise":"quiet"},
      "confidence":0.96
    }]
  }'
```

The response reports `created`, `duplicates`, `rejected`, `replaced` and
`embedding_failures`. This is a deterministic processing API, not an
automatic natural-language extraction model. Fuzzy cross-lingual resolution,
contradiction classification and memory consolidation remain future work.

## 6. Vector and graph operations

### 6.1 Attach an embedding

```bash
curl -X PUT http://127.0.0.1:8765/v1/memories/<MEMORY_UUID>/embedding \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model_id":"BAAI/bge-small-zh-v1.5","embedding":[...]}'
```

The caller generates the vector. Current PostgreSQL profiles support 384 and
512 dimensions. A model ID is permanently associated with one dimension per
tenant. Do not mix embedding model versions under one ID.

### 6.2 Create graph entities and edges

```bash
curl -X POST http://127.0.0.1:8765/v1/entities \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"entity_id":"bedroom-1","entity_type":"room","canonical_name":"Bedroom","aliases":["main bedroom"],"attributes":{}}'

curl -X POST http://127.0.0.1:8765/v1/edges \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"from_entity":"bedroom-1","predicate":"HAS_WINDOW","to_entity":"window-1"}'
```

Edges are canonicalized in PostgreSQL. When AGE is enabled, the API initializes
the projection and projects pending edges. PostgreSQL remains the source of
truth; AGE is a derived graph index.

### 6.3 Hybrid retrieval

```json
{
  "query": "quiet bedroom window",
  "embedding": [0.01, 0.02],
  "model_id": "your-embedding-model",
  "anchor_entity": "bedroom-1",
  "limit": 10
}
```

The vector shown above is illustrative; a real vector must have exactly the
registered dimension. Omit `embedding` or `anchor_entity` if the respective
channel is disabled. The response reports `channels_used` and
`degraded_channels`.

## 7. Objects and evidence

Upload raw bytes:

```bash
curl -X PUT http://127.0.0.1:8765/v1/objects \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @floorplan.png
```

The response returns a SHA-256 digest. Attach it to a claim as
`object_sha256`. Download using:

```bash
curl http://127.0.0.1:8765/v1/objects/<SHA256> \
  -H "Authorization: Bearer $MEMORY_API_KEY" --output evidence.bin
```

The local adapter stores content-addressed files atomically and verifies
checksums. S3-compatible storage is available through the `S3ObjectStore`
adapter. Configure:

```bash
export MEMORY_OBJECT_BACKEND=s3
export MEMORY_S3_BUCKET=your-private-bucket
export MEMORY_S3_REGION=us-east-1
export MEMORY_S3_ENDPOINT_URL=https://s3-compatible-endpoint
export MEMORY_S3_ACCESS_KEY_ID=...
export MEMORY_S3_SECRET_ACCESS_KEY=...
```

Install `.[s3]` to use boto3. Use a private bucket and isolate storage per
tenant/deployment. The current object API enforces a 10 MiB request limit.
Lifecycle, quota, malware scanning and backup policy are deployment concerns.

## 8. Time-series observations

Ingest:

```json
{
  "device_id":"window-1",
  "source_event_id":"gateway-event-001",
  "observed_at":"2026-11-09T10:00:00+08:00",
  "property":"angle",
  "value":17.5,
  "unit":"degree",
  "source_id":"window-gateway"
}
```

POST the payload to `/v1/observations`. Repeating the identical
tenant/device/event ID returns a duplicate acknowledgement; reusing that ID
with different contents is rejected.

Query `GET /v1/observations/{device_id}?property=angle&start_at=...&end_at=...`.
Aggregate with `GET /v1/observations/{device_id}/aggregate?property=angle&start_at=...&end_at=...`.

The current subsystem is a PostgreSQL event-time observation store with
idempotent ingestion, late-arrival ordering and basic count/min/max/average
aggregation. It is not yet a dedicated high-throughput TSDB: automated
partitioning, retention, downsampling and sustained load benchmarks remain
acceptance work.

## 9. Durable events and realtime stream

Poll durable events:

```bash
curl 'http://127.0.0.1:8765/v1/events?limit=100' \
  -H "Authorization: Bearer $MEMORY_API_KEY"
```

Pass the returned `next_cursor` as `after` to continue. SSE:

```bash
curl -N 'http://127.0.0.1:8765/v1/events/stream?max_seconds=30' \
  -H "Authorization: Bearer $MEMORY_API_KEY"
```

The stream is intentionally bounded to 30 seconds. Reconnect using the last
received `id:` as the `Last-Event-ID` header or `after` query parameter.
Clients should de-duplicate by event ID. This is durable polling-backed SSE,
not a push broker; multi-node fan-out and throughput guarantees have not yet
been benchmarked. Browser EventSource cannot set arbitrary Authorization
headers; use fetch streaming or a trusted gateway rather than putting API keys
in query strings.

## 10. Temporal workflow lifecycle

The API can start, describe and request cancellation of the registered
`VerifyMemoryWorkflow`. The Temporal service and Worker are separate processes.

Configure the API:

```bash
export MEMORY_TEMPORAL_ADDRESS=localhost:7233
export MEMORY_TEMPORAL_NAMESPACE=default
export MEMORY_TEMPORAL_TASK_QUEUE=agent-memory-core
```

Install the Temporal extra and start a Worker in a separate process:

```bash
python -m pip install -e '.[temporal,postgres]'
export MEMORY_DSN='postgresql://...'
python -m agent_memory_core.temporal_worker
```

Start a verification:

```bash
curl -X POST http://127.0.0.1:8765/v1/workflows/memory-verifications \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"memory_id":"<existing-claim-uuid>","workflow_id":"verify-claim-001"}'
```

The returned workflow ID is tenant-prefixed by the server. Use that exact ID
for `GET /v1/workflows/{workflow_id}` and
`POST /v1/workflows/{workflow_id}/cancel`. Cross-tenant workflow IDs are
rejected. The current public workflow set contains only memory verification;
general workflow authoring, Signal/Update, arbitrary workflow registration,
worker autoscaling and production Temporal cluster operations are not
implemented. Cancellation is a request to Temporal, not proof that an
external side effect was rolled back.

## 11. Capability discovery and error semantics

Call `GET /v1/capabilities` before using optional channels. A capability
reported unavailable must be treated as unavailable on that instance.

Common status codes:

| Code | Meaning |
|---|---|
| 200 | Read/query/processing succeeded |
| 201 | New claim, observation or entity created |
| 401 | Missing Bearer credential |
| 403 | Invalid API key |
| 409 | Conflicting observation or ambiguous entity |
| 413 | Object exceeds 10 MiB |
| 422 | Invalid payload or timestamp |
| 503 | Optional adapter unavailable or disabled |

## 12. Production acceptance boundary

The current implementation is a functional, testable backend prototype with
a broad authenticated REST facade. Before multi-customer production use, still
require: database RLS and stronger identity, key rotation/revocation, TLS,
rate limiting, audit logs, object ownership metadata, backup/restore, object
lifecycle, outbox DLQ and per-projection task accounting, Temporal worker
operations, memory-processing concurrency controls, Chinese retrieval and
extraction evaluation, and measured p50/p95/load/failure-recovery results.
