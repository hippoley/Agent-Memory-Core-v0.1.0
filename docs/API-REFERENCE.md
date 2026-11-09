# Agent Memory Core — Third-party API & Swagger guide

Version: `1.0.0`  
Status: **local single-tenant REST prototype; not a production public SaaS API**

This document describes only capabilities that a third-party HTTP client can
actually call today. Backend modules that do not yet have HTTP routes are
listed explicitly as not exposed; their existence in Python/SQL is not an
external API commitment.

## 1. Swagger UI and OpenAPI

When the service is running:

- Swagger UI: `http://127.0.0.1:8765/docs`
- ReDoc: `http://127.0.0.1:8765/redoc`
- OpenAPI JSON: `http://127.0.0.1:8765/openapi.json`

Swagger's **Authorize** control accepts the service API key. Enter the key
without the `Bearer ` prefix; Swagger adds the Authorization scheme.

The OpenAPI document is generated from the running app, so its server URL is
the address at which that instance is hosted. Do not publish the development
server directly to the public internet.

## 2. Start a local instance

Requirements: Docker Compose, Python 3.10+, and an available local TCP port
55432.

```bash
git clone https://github.com/hippoley/Agent-Memory-Core-v0.1.0.git
cd Agent-Memory-Core-v0.1.0

docker compose up -d --build --wait
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1

python -m pip install -e '.[postgres,api]'
```

Initialize the canonical PostgreSQL schema:

```bash
export MEMORY_DSN='postgresql://memory:local-dev-only@127.0.0.1:55432/memory'
python -c "import os; from agent_memory_core.postgres import PostgresMemory; db=PostgresMemory(os.environ['MEMORY_DSN']); db.migrate(); db.close()"
```

Set the service credential and start the API:

```bash
export MEMORY_API_KEY='replace-with-a-random-secret-at-least-24-characters'
export MEMORY_TENANT_ID='demo-home'
export MEMORY_ENABLE_VECTOR='1'
export MEMORY_ENABLE_AGE='1'
uvicorn agent_memory_core.server:app --host 127.0.0.1 --port 8765
```

PowerShell environment variables:

```powershell
$env:MEMORY_DSN='postgresql://memory:local-dev-only@127.0.0.1:55432/memory'
$env:MEMORY_API_KEY='replace-with-a-random-secret-at-least-24-characters'
$env:MEMORY_TENANT_ID='demo-home'
$env:MEMORY_ENABLE_VECTOR='1'
$env:MEMORY_ENABLE_AGE='1'
uvicorn agent_memory_core.server:app --host 127.0.0.1 --port 8765
```

The Compose password is a development-only example. Use secrets management,
TLS termination, network restrictions, backups and a hardened database role
before any shared or remote deployment.

## 3. Authentication and tenant boundary

All endpoints except `GET /v1/health` require:

```http
Authorization: Bearer <MEMORY_API_KEY>
```

The API key maps to exactly one server-configured `MEMORY_TENANT_ID`.
Clients cannot choose a tenant in the request body. Unknown fields are
rejected by the write/search request models.

This is **single-tenant service authentication**, not a complete multi-tenant
identity and authorization system. Do not share one key across mutually
untrusted customers. Database RLS, per-user scopes, key rotation/revocation,
audit trails, TLS and rate limits are still required for internet-facing use.

## 4. HTTP endpoints available today

| Method | Path | Auth | Purpose |
|---|---|---:|---|
| GET | `/v1/health` | No | Process liveness only |
| GET | `/v1/ready` | Yes | Authenticated PostgreSQL `SELECT 1` |
| GET | `/v1/capabilities` | Yes | Discover enabled/disabled channels |
| POST | `/v1/memories` | Yes | Append a canonical memory claim |
| GET | `/v1/memories/{subject_id}` | Yes | Read claims for one subject, with time filters |
| POST | `/v1/search` | Yes | SQL + configured vector/graph hybrid retrieval |

### 4.1 Check liveness

```bash
curl http://127.0.0.1:8765/v1/health
```

Expected: `status=alive`. This does **not** prove PostgreSQL is ready.

### 4.2 Check database readiness

```bash
curl -H "Authorization: Bearer $MEMORY_API_KEY" \
  http://127.0.0.1:8765/v1/ready
```

This checks PostgreSQL only. It does not test vector quality, AGE projection,
object storage or Temporal.

### 4.3 Discover actual capabilities

```bash
curl -H "Authorization: Bearer $MEMORY_API_KEY" \
  http://127.0.0.1:8765/v1/capabilities
```

Treat `available: false` as unavailable. Capability flags describe this
server configuration, not every module present in the source repository.

### 4.4 Write a memory claim

```bash
curl -X POST http://127.0.0.1:8765/v1/memories \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "subject_id": "bedroom-1",
    "predicate": "night_preference",
    "value": {"noise": "quiet", "temperature_c": 23},
    "source_id": "conversation:session-001",
    "valid_from": "2026-11-09T00:00:00+08:00"
  }'
```

The response contains the claim UUID. Save it: corrections and future
embedding association need this identifier.

Field semantics:

- `subject_id`: stable entity ID (person, room, device, project, etc.).
- `predicate`: typed relation/fact name.
- `value`: structured JSON value.
- `source_id`: provenance pointer to the originating conversation, file,
  sensor or external event.
- `valid_from`, `valid_to`: real-world validity interval; start inclusive,
  end exclusive. Supply timezone offsets.
- `replaces_id`: optional UUID of an active claim with the same subject and
  predicate; use for correction, not ordinary additive facts.
- `object_sha256`: optional hash reference. It does not upload an object.

### 4.5 Recall a subject's memory

```bash
curl -G http://127.0.0.1:8765/v1/memories/bedroom-1 \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  --data-urlencode 'predicate=night_preference'
```

Bitemporal query:

```bash
curl -G http://127.0.0.1:8765/v1/memories/bedroom-1 \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  --data-urlencode 'valid_at=2026-11-01T20:00:00+08:00' \
  --data-urlencode 'known_at=2026-11-05T12:00:00+08:00'
```

Interpretation: "What did the system know at `known_at` about facts valid
at `valid_at`?" If omitted, both default to the current time.

### 4.6 Hybrid search

Lexical/canonical SQL search:

```bash
curl -X POST http://127.0.0.1:8765/v1/search \
  -H "Authorization: Bearer $MEMORY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"quiet bedroom window","limit":10}'
```

Vector-assisted query (the embedding must be generated by the caller):

```json
{
  "query": "quiet bedroom window",
  "embedding": [0.012, -0.031, 0.044],
  "model_id": "your-embedding-model-id",
  "limit": 10
}
```

The vector above is illustrative only. It is not a valid production vector
unless it has exactly the dimension registered for that model. The current
PostgreSQL adapter supports 384D and 512D profiles. The model ID must remain
stable for one embedding model/version and one dimension.

Graph-assisted query:

```json
{
  "query": "bedroom window",
  "anchor_entity": "bedroom-1",
  "limit": 10
}
```

Graph expansion is bounded to the current adapter's one-hop neighborhood.
The returned `channels_used` tells which channels actually contributed;
`degraded_channels` reports optional channel failures.

**Important current limitation:** the HTTP API has no endpoint to write
embeddings or graph edges. Those indexes must currently be populated through
the Python/PostgreSQL integration path. Therefore a third-party HTTP-only
client can perform SQL search and can query already-populated vector/graph
indexes, but cannot yet build those indexes end-to-end through REST.

## 5. Capabilities present in the backend but not yet exposed as HTTP APIs

These are not Swagger endpoints today:

| Capability | Backend status | Third-party HTTP status | What is missing |
|---|---|---|---|
| Store/query canonical claims | Implemented | Available | Production auth hardening |
| pgvector | DB adapter + query implemented | Partial | Public embedding write endpoint and local encoder integration |
| Apache AGE | Projection/traversal adapter implemented | Partial | Public edge write, projection administration and direct graph query API |
| Object store | Local SHA-256 store implemented | Not exposed | Authenticated upload/download, metadata, lifecycle and remote object adapter |
| Time-series observations | PostgreSQL write/read implemented | Not exposed | Public ingestion/query endpoints and retention/partition policy |
| Temporal | Optional SDK workflow/activities prototype | Not exposed | Public workflow start/status/cancel contract, worker deployment and auth |
| Realtime outbox | SQL outbox exists | Not exposed | Durable cursor, consumer/subscription API, replay and delivery semantics |
| Mem0-inspired memory extraction | Design patterns recorded | Not exposed | Extraction, deduplication, entity resolution and policy pipeline |

Do not interpret a module or database table as a finished external product
feature. The capability endpoint is the runtime authority for advertised
features; this table documents the present HTTP boundary.

## 6. Error handling

| HTTP status | Meaning |
|---|---|
| 200 | Read/search/check succeeded |
| 201 | Claim created |
| 401 | Missing or malformed Bearer credential |
| 403 | Invalid credential |
| 422 | Invalid payload, unsupported timestamp or invalid query parameter |
| 503 | PostgreSQL readiness failure or hybrid adapter not configured |

Optional vector/graph outages are returned in `degraded_channels` when
the configured search adapter can continue with SQL. A canonical SQL failure
must fail the search request; it must not return an empty result as if the
query succeeded.

## 7. Third-party integration pattern

A client should:

1. Obtain a dedicated API key and tenant from the service operator.
2. Call `/v1/health`, then authenticated `/v1/ready`.
3. Call `/v1/capabilities` and disable client features reported unavailable.
4. Store facts with stable subject/predicate/source identifiers.
5. Retain returned claim UUIDs and use `replaces_id` for explicit corrections.
6. Generate embeddings locally using a fixed model/version before vector
   indexing; never mix model versions under one `model_id`.
7. Preserve `source_id` and object hashes so retrieval results can be traced.
8. Treat graph/vector as derived indexes, never as the source of truth.

## 8. Production acceptance gates — not yet passed

Before describing this as a production-ready third-party service, require:

- Per-tenant DB authorization/RLS tests, not only query-level tenant filters.
- TLS, key rotation/revocation, rate limits, request-size limits and audit logs.
- Public APIs for embeddings, graph edges, objects, observations and outbox.
- Temporal worker lifecycle and externally documented workflow operations.
- Object backup, restore, orphan reconciliation and retention/erasure.
- End-to-end tests on one release commit, including process kill/restart,
  duplicate delivery, partial outage and recovery.
- Chinese corpus retrieval evaluation (Recall@k, MRR/nDCG) and p50/p95
  latency/CPU/RAM measurements.
- A pinned, reproducible PostgreSQL/AGE/pgvector image digest.

Until these gates pass, call this a **working research prototype with a
limited authenticated REST facade**, not a fully featured public memory API.
