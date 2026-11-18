# Agent Memory Core — API Reference and Third-party Integration

Contract version: `1.0.0`  
Runtime: FastAPI / OpenAPI 3.x  
Deployment model: local-first, one tenant per API process

This document is the source-oriented guide for the current public HTTP facade.
The generated contract is available at `/openapi.json`. `/docs` serves the
packaged API manual (every operation carries a user story, a runnable example
and a Try-it panel, no CDN required); `/docs/swagger` serves the stock Swagger
UI. Optional channels must be checked through `GET /v1/capabilities`.

## SDK failure contract

`MemoryClient` wraps HTTP and transport failures into `MemoryAPIError`:

| code | status_code | meaning |
|---|---:|---|
| `UNAUTHENTICATED`, `FORBIDDEN` | 401 / 403 | API credential missing or rejected |
| `NOT_FOUND`, `CONFLICT`, `VALIDATION_ERROR` | 404 / 409 / 422 | Request cannot be fulfilled as supplied |
| `RATE_LIMITED`, `UNAVAILABLE` | 429 / 503 | Retry only with a bounded retry policy |
| `TIMEOUT` | 0 | Local transport timed out before an HTTP response |
| `TRANSPORT_ERROR` | 0 | Connection/DNS/transport failure before an HTTP response |
| `INVALID_RESPONSE` | response status | Success response did not contain valid JSON |

`status_code=0` means no HTTP status was received. Do **not** automatically
retry writes without an `Idempotency-Key`: an operation may have committed
before its response was lost. Error messages intentionally omit API key material.
SDK path identifiers cannot contain slash, backslash, or dot-only traversal,
and Unicode/reserved characters are escaped as a single segment.

## 1. Runtime documentation

When the service is running:

- API manual: `http://127.0.0.1:8765/docs`
- Swagger UI: `http://127.0.0.1:8765/docs/swagger`
- OpenAPI JSON: `http://127.0.0.1:8765/openapi.json`

The manual's top-right `API KEY` box and Swagger's Authorize control both take
the key without the `Bearer ` prefix. `/redoc` redirects to `/docs`.

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

The runtime supports two deliberately distinct authentication modes. Do not
mix their configuration or treat the legacy key as an identity-mode credential.

### 3.1 Legacy single-tenant compatibility mode

When `MEMORY_IDENTITY_DSN` is not configured, the legacy local/single-tenant
mode accepts:

```http
Authorization: Bearer <MEMORY_API_KEY>
```

The process binds this key to its server-configured
`MEMORY_TENANT_ID`. This is a compatibility/development mode, not the
multi-user identity control plane.

### 3.2 Identity mode (PR #51)

When `MEMORY_IDENTITY_DSN` is configured, requests use either an opaque
browser Session or a scoped API credential:

```http
Authorization: Bearer <SESSION_OR_API_CREDENTIAL>
X-Memory-Tenant: <WORKSPACE_ID>
```

The browser Observatory may instead send the HttpOnly `amc_session` cookie.
Cookie-authenticated mutations also require the CSRF double-submit cookie and
`X-CSRF-Token` header. The server resolves the current Principal, credential
scope, active Membership and role from PostgreSQL; a caller-supplied tenant
header selects a workspace but never grants access to it.

A first Owner/Admin can issue a short-lived tenant-scoped API credential from
the authenticated identity flow. Ordinary memory clients should receive only
`memory:read` and/or `memory:write`. A worker must receive the dedicated
`tasks:execute` scope; this scope is not interchangeable with ordinary
`memory:write`. Worker mutation routes reject legacy keys and browser sessions,
and require the worker ID plus the current lease epoch on checkpoint and finish. The raw secret is returned once; store it
outside source control. Revoke it with
`DELETE /v1/identity/api-credentials/{credential_id}`. The issuing Principal
must retain the required role, and the credential's scopes and expiry are
enforced on every request. See
[Identity control-plane deployment](IDENTITY-CONTROL-PLANE-DEPLOYMENT.md)
for the separate identity database role and production worker boundaries.

### 3.3 Reproducible local identity walkthrough

From a fresh checkout with Docker Compose and Python available:

```bash
python scripts/launch_local.py up --profile lite --identity
```

Open `http://127.0.0.1:8765/dashboard/observatory.html`, choose **Sign in
with organization**, and register a local Keycloak user. The local Keycloak
admin console is at `http://127.0.0.1:8081`; its development-only bootstrap
credentials are documented by the local Compose profile and must never be
reused outside loopback development. After login, issue an API credential
from the supported Owner/Admin flow and use it with the public SDK/REST API.

To stop the stack without deleting its persistent development volumes:

```bash
python scripts/launch_local.py down
```

This local profile intentionally uses development secrets and loopback HTTP.
It is not a production deployment recipe. Production requires a dedicated
identity-service LOGIN, separate tenant-bound worker credentials, HTTPS,
managed secrets, backup/restore, rate limiting and independent security review.

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
| POST | `/v1/tasks` | Create an idempotent durable business task |
| GET | `/v1/tasks` | List tasks in the authenticated tenant |
| GET | `/v1/tasks/{task_id}` | Read task and latest checkpoint |
| POST | `/v1/tasks/worker/claim` | Claim one task using a dedicated `tasks:execute` credential |
| POST | `/v1/tasks/{task_id}/lease/renew` | Renew the current Worker Lease and Epoch |
| POST | `/v1/tasks/{task_id}/checkpoints` | Commit a checkpoint and Outbox intents with Worker Fencing |
| POST | `/v1/tasks/{task_id}/status` | Finish/pause only with current Worker Fencing |


## 4b. Browsing and trust policy

Two operator-facing reads back the console and any admin UI:

```bash
# Newest-first browse; include_superseded keeps replaced/forgotten claims visible
curl "$BASE/v1/memories?limit=50&include_superseded=true" -H "$AUTH"
curl "$BASE/v1/memories?known_at=2026-11-11T00:00:00Z"      -H "$AUTH"   # what did we believe then
curl "$BASE/v1/memories?subject_id=bedroom&predicate=noise_preference" -H "$AUTH"

# Cardinality is trusted tenant policy, never model output
curl "$BASE/v1/predicate-policies" -H "$AUTH"
curl -X PUT "$BASE/v1/predicate-policies/has_window" -H "$AUTH" \
     -H 'Content-Type: application/json' -d '{"cardinality":"many"}'
```

Unlisted predicates default to `one`. A predicate that already has claims
returns **409** instead of silently reinterpreting history. Extraction
candidates that assert a cardinality different from the stored policy are
rejected, so a model cannot decide that a room may hold two current
temperatures.

## 4c. Console

`GET /dashboard/` serves a same-origin single-page console: ledger with
bitemporal filters, a time-travel viewpoint (`valid_at` / `known_at`), hybrid
search, live event feed, trusted-cardinality toggles and capability discovery.
It is static HTML with no build step and no third-party scripts; the bearer key
is entered once and kept in `localStorage`. Deep links: `#view=search&q=…`,
`#claim=<uuid>`, and `#key=<bearer>` (scrubbed from the address bar on load).
`GET /` redirects there. The API manual is at `/docs`.

`scripts/seed_demo.py --clean --seed` prepares a demo tenant: it soft-forgets
smoke/e2e claims (audit retained) and writes a seven-utterance story that
exercises CREATED, DUPLICATE and CONTRADICTION plus a superseded temperature.

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

The API can start, describe, query progress, cooperatively pause/resume, and request cancellation of the registered
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


The current verification workflow exposes:

- `GET /v1/workflows/{workflow_id}/progress` — stage, completed steps and percentage.
- `POST /v1/workflows/{workflow_id}/pause` — pause at the next safe stage boundary; an in-flight Activity is not interrupted.
- `POST /v1/workflows/{workflow_id}/resume` — continue from the persisted workflow history.

Pause/resume are cooperative workflow signals, not process suspension. Activity-level heartbeat checkpoints for long-running external work are not yet implemented.
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


## 2026-11-18 — Multi-user developer API onboarding (PR #51, Draft)

The original single-process `MEMORY_API_KEY` setup above is a **legacy
compatibility mode**, not the identity model for external developers. On an
identity-enabled instance, developers first log in using the configured OIDC
provider, choose a Workspace and use their authenticated Owner/Admin browser
Session to create a scoped credential. This returns a secret **once only**;
the database persists its hash, not the raw secret.

**Bootstrap with the browser's authenticated Session** (send the HttpOnly
session cookie plus the double-submit CSRF token, not the user password):

```http
POST /v1/identity/api-credentials
X-Memory-Tenant: <your-workspace-id>
X-CSRF-Token: <matching amc_csrf cookie>
Cookie: amc_session=...; amc_csrf=...
Content-Type: application/json

{"name":"my-local-agent","scopes":["memory:read","memory:write"],
 "expires_in_days":30}
```

Response 201 includes `credential_id`, `tenant_id` and `secret`. Save the
secret privately: it will not be returned again. Owner/Admin Session is
required for issuance, and only the issuing Principal may revoke it using
`DELETE /v1/identity/api-credentials/{credential_id}` with the same Session and
CSRF protection. An API Credential cannot mint a second API Credential.

**Third-party SDK integration** (no browser or direct database access needed). The package has not yet been published to PyPI; for PR #51 verification, install the branch build. After publication, switch to the versioned PyPI command:

```bash
pip install 'agent-memory-core[api] @ git+https://github.com/hippoley/Agent-Memory-Core-v0.1.0.git@feat/identity-foundation-schema-20261118'
# After public release:
# pip install 'agent-memory-core[api]>=0.1.0'
export MEMORY_BASE_URL='https://your-memory-host.example'
export MEMORY_TENANT_ID='<the-workspace-id>'
export MEMORY_AGENT_TOKEN='<the-once-returned-amc-secret>'
```

```python
import os
from datetime import datetime, timezone
from agent_memory_core.sdk import MemoryClient

with MemoryClient(
    os.environ["MEMORY_BASE_URL"],
    os.environ["MEMORY_AGENT_TOKEN"],
    tenant_id=os.environ["MEMORY_TENANT_ID"],
) as memory:
    print(memory.whoami())             # Current tenant and role
    print(memory.capabilities())       # Do not assume optional channels
    claim = memory.remember(
        subject_id="user-1", predicate="preferred_language",
        value={"language": "中文"}, source_id="agent:chat:001",
        valid_from=datetime.now(timezone.utc).isoformat(),
        idempotency_key="agent:chat:001:language",
    )
    print(memory.recall("user-1"))      # Canonical current claim
    print(memory.search(query="语言偏好"))  # Search adapter must be enabled
    print(memory.list_memories(include_superseded=True))
    # Optional only when the Temporal capability is deployed:
    if os.getenv("MEMORY_USE_TEMPORAL") == "1":
        job = memory.verify_memory_async(claim["id"])
        print(memory.workflow_status(job["workflow_id"]))
    memory.forget(claim["id"])
```

For direct `curl` clients, send **both** headers on every identity-scoped
request: `Authorization: Bearer <amc-secret>` and
`X-Memory-Tenant: <workspace-id>`. A valid secret cannot select an
unauthorized tenant. `GET /v1/identity/tenants` lets a client discover the
single tenant associated with its API Credential.

The SDK now exposes history browse, identity information, workspaces,
async verification, workflow state and cancellation, in addition to existing
memory operations, SSE events, webhooks, objects and search. All
HTTP endpoints are documented by the runtime's `/openapi.json` and
`/docs/swagger`.

A complete external consumer project, installable independently of the server source tree, is maintained at [`examples/third_party_memory_agent`](../examples/third_party_memory_agent/README.md). Its CLI uses only `agent_memory_core.sdk.MemoryClient`; the Product E2E workflow builds the distributable wheel, provisions a short-lived credential through the real OIDC browser Session, installs the wheel into a fresh virtual environment, and runs that external project against the live API stack. This is the acceptance route for the “no internal imports / no direct database” requirement.

**Important release boundary:** this contract and the PostgreSQL-backed
SDK round-trip are under PR #51 acceptance. It is not a public hosted
multi-tenant SaaS yet. Temporal verification requires its Worker and
persistent backend; production API credentials need TLS, secure identity
deployment, rate limiting and independent restore tests. An SDK client
must never treat a timeout after a write as proof the write did not commit;
use an idempotency key and read back.


## 2026-11-18 — Durable Task Worker Runtime (PR #52, Draft)

PR #52 adds a separately deployable Python worker runtime on top of the
PostgreSQL lease/fencing store. Register application handlers by durable
`task_type`; the runtime claims work, renews the lease from a heartbeat
thread, exposes fenced checkpoint commits to the handler, and records
completion only if the same lease epoch is still current.

The public API separates worker authority from ordinary memory writes:
issue a dedicated credential containing `tasks:execute`. Do not give this
scope to browser users or ordinary memory clients. The claim endpoint is
tenant-scoped by the authenticated credential; callers cannot choose another
tenant by changing the request body.

The integration acceptance includes a real POSIX process test: Worker A
commits a checkpoint and is killed with SIGKILL; after the PostgreSQL lease
expires, Worker B claims a higher epoch, reads the checkpoint, resumes, and
completes. This is a test definition until the corresponding CI job passes;
do not treat a committed test as a passed result.

External effects remain a separate delivery boundary. A handler must persist
an effect intent with the checkpoint and use provider-side idempotency plus
UNKNOWN reconciliation. PostgreSQL fencing cannot roll back a remote action.
