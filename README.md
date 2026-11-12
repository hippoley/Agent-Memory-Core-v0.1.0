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

## Architecture, without the service sprawl

![Agent Memory Core reference architecture](docs/assets/architecture.svg)

```text
                    Agent / SDK / Browser
                            │
                  FastAPI / OpenAPI
                            │
            Structured processing + policies
                            │
           ┌────────────────┴───────────────┐
           │ PostgreSQL — canonical record  │
           │ claims · versions · evidence   │
           │ observed time · outbox events  │
           └────────────────┬───────────────┘
                            │ derived / optional
                ┌───────────┼───────────────┐
               FTS       pgvector       Apache AGE
           lexical       semantic        relations
                └───────────┼───────────────┘
                         trust gate
                            │
               Evidence-bearing retrieval
```

There is **one authoritative record**, not three competing databases. Similarity scores and graph edges discover candidates; canonical tenant/time/provenance checks decide which ones are eligible to be returned.

Evidence bytes live in a local SHA-256-addressed store by default, with optional S3 adapter. Temporal remains a separate opt-in workflow integration, not a required part of every memory operation.

## Try it on your machine

**Requirements:** Python 3.10+, Docker Compose v2. The commands below start the existing PostgreSQL development instance and an API bound to loopback; this is **not** a public deployment recipe.

```bash
git clone https://github.com/hippoley/Agent-Memory-Core-v0.1.0.git
cd Agent-Memory-Core-v0.1.0
docker compose up -d --build --wait
python -m pip install -e '.[postgres,api]'
export MEMORY_DSN='postgresql://memory:local-dev-only@127.0.0.1:55432/memory'
python -c "import os; from agent_memory_core.postgres import PostgresMemory; db=PostgresMemory(os.environ['MEMORY_DSN']); db.migrate(); db.close()"
export MEMORY_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export MEMORY_TENANT_ID='demo-home'
uvicorn agent_memory_core.server:app --host 127.0.0.1 --port 8765
```

Visit **[Memory Console](http://127.0.0.1:8765/dashboard/)**, **[offline API manual](http://127.0.0.1:8765/docs)** or **[Swagger UI](http://127.0.0.1:8765/docs/swagger)**. Enter your **private local** API key only on your own instance; never share it on the public demo.

A **single-command Lite / Standard / Full Compose launcher** is under review in [PR #17](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/pull/17). It must be merged and verified before the above setup can be replaced by `python scripts/launch_local.py up --profile lite`.

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

The best issues here are concrete failures of memory **correctness**, not new feature wish lists: an outdated claim that still gets recalled, an impossible historical interval, a replay that duplicates a fact, an incorrect tenant boundary, or a resource measurement others can reproduce. See [Contributing](CONTRIBUTING.md) and [Security](SECURITY.md).\n\n## Explore the project\n
| Start here | Deep dive | Build / verify |
| :--- | :--- | :--- |
| [Interactive Memory Atlas](docs/demo/index.html) | [Architecture deep dive · 中文](docs/ARCHITECTURE-DEEP-DIVE.zh-CN.md) | [Python SDK](docs/PYTHON-SDK.md) |
| [中文快速导览](docs/START-HERE.zh-CN.md) | [Memory processing semantics](docs/MEMORY-PROCESSING.md) | [API reference](docs/API-REFERENCE.md) |
| [What is actually implemented?](docs/CAPABILITY-ACCEPTANCE-AUDIT.md) | [Lightweight architecture ADR](docs/ADR-0004-lightweight-memory-platform.md) | [Release acceptance gates](docs/USER-STORY-ACCEPTANCE.md) |
| [Public demo deployment](docs/PUBLIC-DEMO-DEPLOYMENT.md) | [Design decisions](docs/ADR-0002-postgres-triple-store.md) | [Full engineering reference](docs/REFERENCE-README.md) |

---

<div align="center">

**Memory shouldn't just survive a session. It should survive being wrong.**

Apache-2.0 · Open-source, local-first agent memory infrastructure

</div>
