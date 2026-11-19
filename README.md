<div align="center">

<img src="docs/assets/hero-belief-lifecycle.svg" alt="Evidence-grounded belief revision with preserved memory history" width="100%" />

# Agent Memory Core

### Change the belief. Keep the evidence. Remember what was true — and when.

**An API-first, local-first memory runtime for AI agents.** Store versioned claims, revise them with provenance, retrieve current state and history, and run durable verification through one public REST API and Python SDK.

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB)](pyproject.toml)
[![CI](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/actions/workflows/test.yml/badge.svg)](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/actions/workflows/test.yml)
[![Development status](https://img.shields.io/badge/status-active%20development-orange)](docs/USER-STORY-ACCEPTANCE.md)

[Quickstart](#quickstart) · [Third-party SDK](#connect-an-agent-with-the-python-sdk) · [API & Swagger](#api-and-observability) · [Architecture](#architecture) · [Observatory](#memory-observatory--see-memory-not-just-messages) · [中文导览](docs/START-HERE.zh-CN.md)

</div>

---

## Why Agent Memory Core?

An agent learns that a supplier is paid monthly. A new contract changes the term to quarterly. A conventional vector store may retrieve both statements with similar confidence — but an agent needs to know which belief is current, why it changed, and what it believed yesterday.

Agent Memory Core treats memory as a **versioned, evidence-carrying belief**, not just a chunk of text:

- **Current state:** retrieve the claim that is valid now.
- **Revision history:** preserve superseded versions instead of silently overwriting them.
- **Evidence and provenance:** trace a claim to the source that supports it.
- **Bitemporal recall:** ask what was true at one time and what the system knew at another.
- **Tenant-aware API:** let independent agents use scoped credentials without database access.
- **Durable work:** expose events and optional Temporal verification through public APIs.

> **Models propose. The memory runtime validates, commits and preserves the evidence.**

The project does not claim universal truth detection or reliable natural-language understanding across every domain. Extraction quality depends on the caller's model or rules; the canonical memory layer provides deterministic identity, revision, scope and history semantics.

## What you can build

| Use case | What the runtime provides |
|---|---|
| Personal or workspace memory | Durable claims, current recall and historical versions |
| Agent correction | Explicit revision with source linkage and idempotency |
| Evidence-grounded answers | Provenance lookup and binary evidence objects |
| Hybrid retrieval | PostgreSQL full-text search, optional pgvector and Apache AGE |
| Long-running verification | Events, workflow status and optional Temporal workers |
| Multiple independent agents | Public REST API, Python SDK and tenant-scoped credentials |

This is a memory component for your existing agent stack — not a replacement for LangGraph, AgentScope, an LLM, or your application.

## Quickstart

The fastest path is a local development deployment. It uses Docker Compose and does not require a hosted account.

**Prerequisites:** Git, Docker Compose v2, Python 3.10+.

### 1. Start the runtime

```bash
git clone https://github.com/hippoley/Agent-Memory-Core-v0.1.0.git
cd Agent-Memory-Core-v0.1.0

# Fast local memory + API profile
python scripts/launch_local.py up --profile lite
```

For the integrated identity, browser login and Temporal development flow:

```bash
python scripts/launch_local.py down
python scripts/launch_local.py up --profile extended --identity
```

The launcher provisions the local services and writes development configuration to the ignored `.env.local`. Wait for the stack to become healthy before opening the UI.

### 2. Open the interfaces

| Interface | Local URL | Purpose |
|---|---|---|
| Console | [127.0.0.1:8765/dashboard/](http://127.0.0.1:8765/dashboard/) | Browse and search memory |
| Observatory | [127.0.0.1:8765/dashboard/observatory.html](http://127.0.0.1:8765/dashboard/observatory.html) | Inspect memory and evidence |
| API manual | [127.0.0.1:8765/docs](http://127.0.0.1:8765/docs) | Runnable API examples |
| Swagger UI | [127.0.0.1:8765/docs/swagger](http://127.0.0.1:8765/docs/swagger) | Explore HTTP operations |
| OpenAPI | [127.0.0.1:8765/openapi.json](http://127.0.0.1:8765/openapi.json) | Machine-readable contract |

The local development API key is generated into `.env.local`. Use the identity-enabled profile and sign in through the configured local OIDC provider when testing multi-user workspaces and scoped API credentials.

### 3. Make your first SDK call

Install the project in a virtual environment:

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1

python -m pip install -e '.[api,postgres]'
```

### 4. Verify the running product end to end

This is an actual API/SDK smoke journey, not a CI status check. It creates a uniquely named memory, revises it, verifies current recall and retained history, calls search, reads evidence and events, and exits non-zero on the first broken product operation:

```bash
python scripts/smoke_local.py
```

A successful run ends with `LOCAL_MEMORY_RUNTIME_ACCEPTED`. It first authenticates the PostgreSQL readiness probe, then exercises the object-store upload/download round-trip, binds the evidence digest to a claim, revises that claim, and verifies current recall, retained history, search, provenance and events. It talks only to the local HTTP API through the public SDK; it does not connect to PostgreSQL directly. It leaves uniquely named demo records in the local database so they can be inspected in Observatory.

For durable local Temporal execution (separate server + Worker, Workflow History retained in a named Docker volume across container replacement), use the extended profile:

```bash
python scripts/launch_local.py up --profile extended
```

See [Temporal Durable Execution and Recovery](docs/TEMPORAL-DURABLE-EXECUTION.md) for restart, backup, restore boundaries and the exact distinction between Workflow replay and resuming an interrupted Activity.

Then connect to the local API using the development key from `.env.local`:

```python
import os
from datetime import datetime, timezone
from agent_memory_core.sdk import MemoryClient

with MemoryClient(
    "http://127.0.0.1:8765",
    os.environ["MEMORY_API_KEY"],
) as memory:
    claim = memory.remember(
        subject_id="supplier-a",
        predicate="settlement_period",
        value={"period": "monthly"},
        source_id="contract:version-1",
        valid_from=datetime.now(timezone.utc).isoformat(),
        idempotency_key="supplier-a:settlement:v1",
    )

    print("Current:", memory.recall("supplier-a"))
    print("History:", memory.list_memories(
        subject_id="supplier-a",
        include_superseded=True,
    ))
```

The local single-key configuration is a convenient development/legacy mode. For independent agents and multi-user identity, use a tenant-scoped API credential as described below.

To stop the local stack:

```bash
python scripts/launch_local.py down
```

## Run the product locally in a browser

For a hands-on local experience, install the API/SDK dependencies and run the demo launcher:

```bash
python -m pip install -e '.[api,postgres]'
python scripts/demo_local.py
```

The launcher starts the loopback-only Docker Compose stack, runs a real SDK lifecycle (create → revise → current/history readback → search → evidence object round-trip → events), and opens the Console, Observatory and API manual in your browser. It leaves uniquely named smoke records in the local database so you can inspect them; it does not call a hosted model or publish the API to the Internet.

- **Console:** `http://127.0.0.1:8765/dashboard/` — natural-language ingestion, claim ledger, history and search.
- **Observatory:** `http://127.0.0.1:8765/dashboard/observatory.html` — start in clearly labeled synthetic Demo mode, then connect with the local key to inspect Live API data.
- **API manual:** `http://127.0.0.1:8765/docs`.

The key is generated locally in `.env.local` as `MEMORY_API_KEY`; paste it only into the local browser page. Stop the stack with `python scripts/launch_local.py down`. See [the Chinese local demo guide](docs/LOCAL-DEMO.zh-CN.md) for profiles, identity mode and safety boundaries.

## Connect an agent with the Python SDK

External agents use the same public REST API as Observatory. They do not import server modules, access PostgreSQL, or use browser-only business endpoints.

### Install

The SDK is not yet published to PyPI. During PR #51 development, install from the integration branch:

```bash
python -m pip install 'agent-memory-core[api] @ git+https://github.com/hippoley/Agent-Memory-Core-v0.1.0.git@feat/identity-foundation-schema-20261118'
```

When a versioned PyPI release is published, the installation command will move to the versioned package. Do not assume `agent-memory-core==0.1.0` on PyPI is this repository's distribution.

### Issue a tenant-scoped credential

1. Sign in to an identity-enabled deployment using its configured OIDC provider.
2. Open the intended Workspace as an Owner or Admin.
3. Create an API credential with the required scopes (normally `memory:read` and `memory:write`) and an expiry.
4. Copy the returned secret immediately. It is shown once; the server stores its hash.
5. Keep the secret in an environment variable or secret manager — never commit it or embed it in frontend code.

An API credential cannot mint another credential. Credential issuance and revocation require an authenticated Owner/Admin browser Session and CSRF protection. See the [API reference](docs/API-REFERENCE.md) for the exact HTTP contract.

### Run the independent consumer example

The repository includes a separately installable consumer project:

[`examples/third_party_memory_agent`](examples/third_party_memory_agent/README.md)

It imports only `agent_memory_core.sdk.MemoryClient` and exercises readiness, identity, capabilities, create, revise, current/history readback, search, provenance, events, optional Temporal verification, forget and retained-history verification.

Configure it with:

```bash
export MEMORY_BASE_URL='http://127.0.0.1:8765'
export MEMORY_TENANT_ID='your-workspace-id'
export MEMORY_API_KEY='your-once-issued-credential'
python -m pip install ./examples/third_party_memory_agent
python -m third_party_memory_agent
```

For Temporal verification, set `MEMORY_RUN_TEMPORAL=1` only when the deployment advertises the Temporal capability and its worker is running.

The independent consumer has completed its real API lifecycle in the PR #51 Product E2E environment, including loading the SDK from the clean virtual environment's `site-packages`. The broader Product E2E job also contains a queued-work revocation check; its latest executor fix is awaiting a fresh run. See [PR #51](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/pull/51) for current evidence. This is not a claim that the full release gate or production deployment is complete.

## Memory Observatory — See memory, not just messages

**Memory Observatory** is the visual inspection surface for the memory runtime: browse claims, inspect provenance, filter the records currently loaded, and follow durable events.

- [Open the Observatory source](src/agent_memory_core/resources/dashboard/observatory.html)
- [Open the interactive Memory Atlas](docs/demo/index.html) — a local, credential-free conceptual walkthrough using illustrative data.
- [Design system and visual semantics](docs/DESIGN-SYSTEM.md)
- [Architecture review and trust boundaries](docs/ARCHITECTURE-REVIEW.md)

The Observatory work originated in **PR #30**; the API-first identity and third-party integration work continues in **PR #51**. Local Chromium acceptance passed on 2026-11-18 for the documented browser path. This does not mean a public hosted service exists: **no public hosted deployment is claimed**. The Atlas is explanatory and synthetic; it is not a live tenant dashboard or a screenshot of production data.

To inspect the UI locally, start the stack and open `http://127.0.0.1:8765/dashboard/observatory.html`. For a credential-free public preview, use the committed [Memory Atlas](docs/demo/index.html). For a local authenticated API walkthrough, replace the placeholder below with the key generated in your own ignored `.env.local` file; never paste a real key into README or commit it:

```bash
export MEMORY_API_KEY='YOUR_LOCAL_API_KEY'
curl http://127.0.0.1:8765/v1/ready \
  -H "Authorization: Bearer $MEMORY_API_KEY"
```

The current visual preview does not yet guarantee complete-tenant visualization, graph-edge truth, or production authorization semantics. See [the architecture review](docs/ARCHITECTURE-REVIEW.md) for the explicit boundaries.

## API and observability

The REST API is the product boundary. OpenAPI is the interface contract; the Python SDK is a client, not a second implementation of business logic.

- [API reference and third-party integration](docs/API-REFERENCE.md)
- [API-first product contract](docs/API-FIRST-PRODUCT-CONTRACT.md)
- [Swagger guide (中文)](docs/SWAGGER_GUIDE.zh-CN.md)
- Runtime OpenAPI: `/openapi.json`
- Runtime API manual: `/docs`
- Runtime Swagger UI: `/docs/swagger`

Useful operations include:

| Capability | SDK method | API behavior |
|---|---|---|
| Identity | `whoami()`, `workspaces()` | Read authenticated identity and workspaces |
| Create / revise | `remember()`, `revise()` | Commit canonical versioned claims |
| Current / history | `recall()`, `list_memories()` | Read active state and superseded versions |
| Search | `search()` | SQL plus enabled retrieval channels |
| Evidence | `sources()`, `upload_object()`, `download_object()` | Read provenance and evidence objects |
| Events | `events()` | Read durable event history |
| Async verification | `verify_memory_async()`, `workflow_status()` | Start and inspect Temporal verification |
| Workflow control | `cancel_workflow()` | Request cancellation |
| Webhooks | SDK webhook methods | Configure and operate event delivery |

Check `GET /v1/capabilities` before using optional vector, graph, object-storage or Temporal features. The API returns the actual availability of each capability.

## Architecture

![Bsph Blue runtime topology: agents, deterministic processor, PostgreSQL authority, optional pgvector, Apache AGE and Temporal adapters](docs/assets/architecture.svg)

![Bsph Blue lifecycle: source and candidate, canonical write, asynchronous projections, and verified recall](docs/assets/memory-lifecycle.svg)

**One canonical authority, multiple retrieval views.** PostgreSQL owns committed claims and revision history. Full-text, vector and graph indexes help discover candidates; they do not override canonical tenant and time eligibility.

```text
Any agent / REST / Python SDK
             |
       Public API + identity
             |
    Candidate + evidence checks
             |
    Deterministic revision guard
             |
 PostgreSQL — canonical claims, provenance,
      bitemporal history and outbox
       |             |             |
      FTS          pgvector    Apache AGE
       \____________|____________/
                    |
          Canonical-filtered recall
                    |
             Agent / consumer

     Temporal — optional durable verification
```

### Core lifecycle

1. **Capture:** associate a candidate with a source and tenant.
2. **Validate:** check identity, scope, policy and revision preconditions.
3. **Commit:** persist the canonical claim and history transactionally.
4. **Project:** update retrieval views through recoverable asynchronous work.
5. **Recall:** retrieve candidates, then filter against canonical state and bitemporal rules.
6. **Verify:** optionally run durable Temporal workflows and preserve verification receipts.

## Deployment profiles

| Profile | PostgreSQL | Vector / AGE retrieval | Projection worker | Temporal |
|---|---|---|---|---|
| Lite | Yes | Off | No | No |
| Standard | Yes | On when providers/extensions are configured | No | No |
| Full | Yes | On when providers/extensions are configured | Yes | No |
| Extended | Yes | On when configured | Yes | Local development server + verification worker |

Start a profile with `python scripts/launch_local.py up --profile <lite|standard|full|extended>`. Add `--identity` to enable the local identity/OIDC development stack. Extended is intended for integrated development and acceptance, not a production Temporal operations recipe.

Lite still uses the PostgreSQL database image; it is not SQLite-only or a measured low-memory guarantee. See [runtime profiles and limitations](docs/LIGHTWEIGHT-RUNTIME.md).

## What is implemented — and what is not

### Working foundations

- Public REST API and OpenAPI contract for core memory operations.
- Python SDK for identity-aware API access, memory lifecycle, search, evidence, events and workflow operations.
- PostgreSQL canonical claims, provenance, revision history and outbox.
- Real independent third-party consumer example; its create/revise/read/search/provenance/events/forget lifecycle has run against the integrated API stack.
- Local Observatory and browser-based OIDC integration.
- Optional pgvector, Apache AGE and Temporal integration paths.

### Still being productized

- A fully repeatable clean-machine deployment and Owner bootstrap experience.
- Production-ready shared multi-tenant worker routing and complete worker authorization across every projection.
- Webhook delivery operations, dead-letter handling and replay under failure.
- Fault-injected process/database recovery and independently verified backup/restore.
- Invitation onboarding, full membership administration and complete resource-level RBAC.
- External OIDC provider compatibility, production secret rotation, rate limiting, TLS deployment hardening and independent security review.
- Cross-domain model extraction/retrieval quality and measured load/latency/recovery targets.

The repository is active development software. It does not yet guarantee production-grade multi-tenant isolation or operational recovery. It is not yet a hosted public SaaS or a security-reviewed multi-tenant production service. Do not expose the local development stack directly to the Internet or put regulated/production secrets into a demo deployment.

## Evaluation and engineering evidence

The repository preserves fixed Chinese extraction and multi-turn evaluation sets. The [200-case rule-extractor replay](data/eval/ZH-200-RULE-REPLAY-20261116.md) measured **94.34% extraction F1** on its stated in-scope A/B/C examples; this is not end-to-end memory quality, real-model quality or a general benchmark. Out-of-vocabulary exact coverage was 0/30.

Run the basic test suite:

```bash
python -m pip install -e '.[dev,postgres,api]'
pytest -q
```

For integration tests, use a disposable PostgreSQL / pgvector / AGE environment and set `MEMORY_TEST_DSN` as described in the [acceptance guide](docs/USER-STORY-ACCEPTANCE.md).

## Contributing

The most valuable contributions are reproducible improvements to the public product path:

- A third-party SDK call that fails against the documented API.
- A revision that leaves stale state active or loses provenance.
- A tenant boundary or revoked identity that can still perform an operation.
- A worker crash/retry that loses or duplicates a durable side effect.
- A clean deployment or restore procedure that another developer cannot reproduce.
- A retrieval evaluation with a frozen dataset and honest metrics.

See [CONTRIBUTING](CONTRIBUTING.md), [SECURITY](SECURITY.md), [User Story acceptance](docs/USER-STORY-ACCEPTANCE.md), and the [engineering roadmap](docs/AGE-TEMPORAL-STRATEGY.md).

---

<div align="center">

**Memory shouldn't just survive a session. It should survive being wrong.**

[中文导览](docs/START-HERE.zh-CN.md) · [Interactive Atlas](docs/demo/index.html) · [API reference](docs/API-REFERENCE.md) · [Report an issue](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/issues)

</div>


## Durable Task Worker Runtime (PR #52, Draft)

The PostgreSQL task store now has an independent worker runtime with
lease/heartbeat renewal, epoch-fenced checkpoint commits and process-level
recovery. Worker credentials require the explicit `tasks:execute` scope;
ordinary memory write credentials cannot claim or mutate durable tasks.

- [Standalone worker handler example](examples/durable_task_worker/README.md)
- [API and worker authorization contract](docs/API-REFERENCE.md)
- Runtime entrypoint: `python -m agent_memory_core.worker_runtime`

The SIGKILL acceptance test starts Worker A as a separate OS process, kills it
after a durable checkpoint, then starts Worker B to reclaim the expired lease
and resume from that checkpoint. The test passed in the PostgreSQL integration workflow on HEAD
`3e1bce2db53e074baf0c7ba8d3755cc02b67341f`. The integration workflow
reported 62 passed and 7 skipped; skipped tests are not counted as passing. Provider-side idempotency and
UNKNOWN reconciliation remain separate release gates; a database lease does
not make a remote side effect exactly once.


## External effect safety (Migration 008)

Durable checkpoints can record external effect intents transactionally.
`agent_memory_core.durable_effects` adds fenced provider execution and
UNKNOWN reconciliation. An expired Worker does not trigger an automatic
provider retry: the replacement Worker first reconciles the prior idempotency
key. Only a definitive provider `NOT_FOUND` returns the effect to
`requested`; task completion is blocked until every effect is committed.

Provider adapters must implement `execute(request, idempotency_key)` and
`reconcile(request, idempotency_key, provider_reference)`. The stable key
is scoped to tenant + task + effect. Exactly-once remote execution is not
claimed; it depends on provider-side idempotency and reconciliation semantics.


The fencing boundary is enforced inside the canonical Python task store too:
checkpoint and status mutations require the current Worker ID and Lease Epoch.
The HTTP layer is not the only guard; a tenant-only SDK/store write cannot
bypass the PostgreSQL lease check.
