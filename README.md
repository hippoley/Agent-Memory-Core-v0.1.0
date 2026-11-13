<div align="center">

<img src="docs/assets/hero-belief-lifecycle.svg" alt="Agent Memory Core — Agents change their minds. Memory should know how." width="100%" />

<br />

**Belief revision · Evidence-bound recall · One canonical authority**

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache--2.0-94dcc5?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-92a9bc?style=flat-square)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-research%20prototype-e6bd80?style=flat-square)](docs/USER-STORY-ACCEPTANCE.md)
[![Tests](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/actions/workflows/test.yml/badge.svg)](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/actions/workflows/test.yml)

[**Explore the interactive walkthrough**](docs/demo/index.html) · [**Architecture deep dive**](docs/ARCHITECTURE-DEEP-DIVE.zh-CN.md) · [**API reference**](docs/API-REFERENCE.md) · [**中文导览**](docs/START-HERE.zh-CN.md)

</div>

---

## The problem is not forgetting. It's believing the wrong thing.

A vector store can retrieve yesterday's preference just as confidently as today's correction. An agent can remember *both* — and still act on the wrong one.

**Agent Memory Core is an experimental system of record for evolving agent beliefs.** It tracks what an agent was told, when the claim was valid, when the system learned it, what superseded it, and which evidence supports it. Retrieval must pass through canonical fact and time constraints; similarity alone never grants authority.

**Change the belief. Keep the history.**

### The 20-second story

| At 10:00 | At 10:20 | Ask about 10:10 |
| :--- | :--- | :--- |
| “I prefer a quiet bedroom.” | “Actually, soft music is fine.” | “What did the agent believe then?” |
| **Current:** quiet | **Current:** soft music; **past:** preserved | **Historical answer:** quiet |

This is an illustrative bitemporal scenario, not an assertion that the system automatically understands every natural-language correction. The canonical revision path is implemented; real multilingual model-quality evaluation remains an open gate.

## What makes this different

| Mechanism | Why it matters | Where to verify |
| :--- | :--- | :--- |
| **Belief revision** | Corrections change current truth without rewriting historical knowledge | [Bitemporal design](docs/ARCHITECTURE-DEEP-DIVE.zh-CN.md) |
| **Recall with receipts** | Each fact carries provenance; retrieval must respect tenant, validity and source constraints | [Memory processing](docs/MEMORY-PROCESSING.md) |
| **One authority, multiple views** | PostgreSQL commits canonical facts; FTS, pgvector and Apache AGE are derived retrieval views | [Architecture decision](docs/ADR-0004-lightweight-memory-platform.md) |
| **Failure-aware writes** | Transactional outbox, retries and idempotency separate committed facts from recoverable projections | [Acceptance contract](docs/USER-STORY-ACCEPTANCE.md) |
| **Small operational footprint by design** | Local object storage, optional model integration and Temporal; no required Redis/Kafka/Neo4j fleet | [Lightweight architecture](docs/ADR-0004-lightweight-memory-platform.md) |
| **Bring your own agent** | Authenticated REST, Python SDK, OpenAPI and event subscriptions, independent of a single agent framework | [API reference](docs/API-REFERENCE.md) |

## Architecture: one authority, clear boundaries

The design makes one deliberate choice: **the database decides what is recorded; indexes only help find it**. Keeping this distinction visible is more important than decorating the diagram with technology logos.

### 01 / Runtime topology — what actually runs where

![Architecture with API, processing, canonical PostgreSQL, in-database FTS/pgvector/AGE and optional adapters](docs/assets/architecture.svg)

The Console and third-party Agents are both clients of the FastAPI boundary. Processing, revision and provenance are logical application responsibilities; they do **not** require separate microservices. **PostgreSQL + pgvector + Apache AGE are one compatible database installation**, not three independent data servers.

Outside that database: evidence bytes use a local SHA-256 object store by default; extraction/embedding providers, projection workers and Temporal workflows are optional adapters. A configured provider or feature flag does not prove that every channel is available on a particular deployment.

### 02 / Data lifecycle — why the boundaries exist

![Fact write, correction, asynchronous projections and canonical-filtered recall](docs/assets/memory-lifecycle.svg)

1. **Propose:** an Agent supplies structured candidate facts or calls an explicitly configured model/rules extractor.
2. **Decide:** the deterministic pipeline validates candidates, resolves trusted predicate cardinality, deduplicates or creates a versioned correction.
3. **Commit:** PostgreSQL persists canonical claims, source links, record times and transactional outbox. **This is the persistence boundary.**
4. **Project:** optional workers index FTS, vectors and graph edges; these views may lag or degrade independently.
5. **Recall:** SQL/FTS/vector/graph generate candidates, then the hybrid path reapplies **tenant, effective-time, recorded-time and supersession filters** before returning eligible results. `source_id` is returned; `/v1/memories/{memory_id}/sources` exposes supporting provenance.

**What this does not yet guarantee:** universal per-claim authorization, independent verification that sources are truthful, automatic correct natural-language contradiction handling or a completed production security audit. In RLS mode, the current server deliberately disables the AGE graph adapter rather than assuming graph queries are secure by default.

For the design invariants, precise code mapping and unresolved risks, see the [architecture review](docs/ARCHITECTURE-REVIEW.md) and [architecture deep dive](docs/ARCHITECTURE-DEEP-DIVE.zh-CN.md).

## Try it on your machine

**Requirements:** Python 3.10+ and Docker Compose v2. The **unified integration branch** now contains the local runtime from PR #17. For a local-only database, API, Console and Swagger:

```bash
git clone https://github.com/hippoley/Agent-Memory-Core-v0.1.0.git
cd Agent-Memory-Core-v0.1.0
git switch integrate/agent-memory-core-unified-20261113
python scripts/launch_local.py up --profile lite
```

Visit **[Memory Console](http://127.0.0.1:8765/dashboard/)**, **[offline API manual](http://127.0.0.1:8765/docs)** or **[Swagger UI](http://127.0.0.1:8765/docs/swagger)**. The launcher generates a random bearer key in ignored `.env.local`; do not share it. The existing PostgreSQL administrator credentials are strictly for local development.

Switch to `--profile standard` for optional vector/graph query channels, or `--profile full` for an extra projection worker. No model server or Temporal cluster is automatically installed. The Lite profile still uses the combined PostgreSQL extension image — do not interpret it as an extension-free or production-hardened database.

```bash
python scripts/benchmark_resources.py --requests 200 --concurrency 4
```

This records real Docker CPU/memory snapshots, local data volumes and read-only authenticated API latency. It **does not** prove peak RAM, write P95, inference latency or a general performance advantage. See [Lightweight Runtime](docs/LIGHTWEIGHT-RUNTIME.md) for precise measurement limitations.

### Remember, then recall

```python
from datetime import datetime, timezone
from agent_memory_core.sdk import MemoryClient

with MemoryClient("http://127.0.0.1:8765", "YOUR_LOCAL_API_KEY") as memory:
    claim = memory.remember(
        subject_id="bedroom",
        predicate="night_preference",
        value={"noise": "quiet"},
        source_id="conversation:001",
        valid_from=datetime.now(timezone.utc).isoformat(),
        idempotency_key="conversation:001:night_preference",
    )
    print(claim["id"])
    print(memory.recall("bedroom"))
```

Run the [real HTTP belief-revision example](examples/belief_revision.py) with `MEMORY_API_KEY` to write a claim, replace it and inspect provenance. Use the [real API reference](docs/API-REFERENCE.md) for historical reads, evidence and event subscriptions. The [public static walkthrough](docs/demo/index.html) uses synthetic examples; it never requests credentials and is not a hosted live API.

## Latest main-line evaluation work

The integration branch is based on the newer `main` commit `643b6de`, retaining the **weighted retrieval-channel fusion, OR-semantics FTS, backdated ingestion and LongMemEval-24 evaluation tooling**. The README design work does not overwrite those modules.

- [LongMemEval-24 dataset and run summary](data/eval/longmemeval_24.md)
- [Cloud evaluation run artifacts](data/eval/longmemeval_cloud24_p012.json)
- [Evaluation tooling](scripts/eval/run_longmemeval.py)
- [Full latest-main engineering reference](docs/REFERENCE-README.md)

These are experiment artifacts, not evidence that all memory accuracy or production gates have passed.

## From idea to evidence

```bash
python -m pip install -e '.[dev,postgres,api]'
pytest -q
# With a running isolated PostgreSQL + pgvector + AGE stack:
export MEMORY_TEST_DSN='postgresql://memory:local-dev-only@127.0.0.1:55432/memory'
pytest -q -m integration
```

| Already implemented / testable | **Not yet release-qualified** |
| :--- | :--- |
| Canonical claim writes, provenance and basic bitemporal revisions | Real-world Chinese extraction and retrieval quality on judged 200+ item corpora |
| PostgreSQL + pgvector + AGE integration paths | Host-specific RAM/CPU/disk and measured end-to-end latency superiority |
| Authenticated REST / SDK / console / Swagger | Public untrusted multi-customer SaaS deployment, full key lifecycle and security |
| Durable events, bounded subscriptions and replay paths | Complete fault-injection, backup/restore and production reliability gates |

The repository is an **executable research prototype**. A passing unit or integration test does not prove model accuracy, deployment security or an SLA. See the [full acceptance matrix](docs/USER-STORY-ACCEPTANCE.md) and [capability audit](docs/CAPABILITY-ACCEPTANCE-AUDIT.md).

## Get involved

The best issues here are concrete failures of memory **correctness**, not new feature wish lists: an outdated claim that still gets recalled, an impossible historical interval, a replay that duplicates a fact, an incorrect tenant boundary, or a resource measurement others can reproduce. See [Contributing](CONTRIBUTING.md), [Security](SECURITY.md) and the [branch consolidation audit](docs/BRANCH-CONSOLIDATION-20261113.md).\n\n## Explore the project\n
| Start here | Deep dive | Build / verify |
| :--- | :--- | :--- |
| [Interactive Memory Atlas](docs/demo/index.html) | [Architecture deep dive · 中文](docs/ARCHITECTURE-DEEP-DIVE.zh-CN.md) | [Python SDK](docs/PYTHON-SDK.md) |
| [中文快速导览](docs/START-HERE.zh-CN.md) | [Memory processing semantics](docs/MEMORY-PROCESSING.md) | [API reference](docs/API-REFERENCE.md) |
| [What is actually implemented?](docs/CAPABILITY-ACCEPTANCE-AUDIT.md) | [Lightweight architecture ADR](docs/ADR-0004-lightweight-memory-platform.md) | [Release acceptance gates](docs/USER-STORY-ACCEPTANCE.md) |
| [Public demo deployment](docs/PUBLIC-DEMO-DEPLOYMENT.md) | [Architecture review / correctness risks](docs/ARCHITECTURE-REVIEW.md) | [Branch consolidation audit](docs/BRANCH-CONSOLIDATION-20261113.md) |

---

<div align="center">

**Memory shouldn't just survive a session. It should survive being wrong.**

Apache-2.0 · Open-source, local-first agent memory infrastructure

</div>
