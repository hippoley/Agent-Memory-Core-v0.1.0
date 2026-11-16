<div align="center">

<img src="docs/assets/hero-belief-lifecycle.svg" alt="Agent Memory Core — agents change their minds, memory should keep the evidence" width="100%" />

# Agent Memory Core

### Agents change their minds. Memory should keep the evidence.

**A local-first, model-agnostic memory runtime for evolving agent beliefs — with evidence-grounded revisions, Apache AGE graph retrieval, and durable verification.**

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB)](pyproject.toml)
[![CI](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/actions/workflows/test.yml/badge.svg)](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/actions/workflows/test.yml)
[![Research prototype](https://img.shields.io/badge/status-research%20prototype-orange)](docs/USER-STORY-ACCEPTANCE.md)

[**Get started**](#quickstart) · [**Explore the interactive Memory Atlas**](docs/demo/index.html) · [**Architecture**](#architecture) · [**AGE + Temporal**](#first-class-extensions-apache-age-and-temporal) · [**API reference**](docs/API-REFERENCE.md) · [**中文导览**](docs/START-HERE.zh-CN.md)

</div>

---

## Your agent remembered the correction. Why does it still recall the mistake?

An agent learns that a supplier is paid monthly. The contract changes to quarterly. A vector search can now retrieve **both statements** with equal confidence — including the one that should no longer drive an action.

The issue isn't storing more history. It's **knowing which belief is current, what evidence changed it, and what was known at a particular point in time**.

**Agent Memory Core** experiments with a different primitive: a versioned, evidence-carrying belief. Language models *propose* changes; a deterministic memory layer checks identity, evidence, scope and current state before committing them. Text, vectors and graph edges help find candidates, but similarity alone never decides what is true.

> **Change the belief. Keep the history.**

### One story, three questions

| Earlier | Later | What you should be able to ask |
| --- | --- | --- |
| “Supplier A settles monthly.” | “The contract now says quarterly.” | **Current:** What is the latest settlement rule? |
| Source 1 supports the old claim | Source 2 supports the correction | **Why:** What evidence supports the new rule? |
| The old claim was active yesterday | The new one is active today | **Then:** What did the agent know yesterday? |

The story illustrates the intended semantics. The repository implements structured revision and an opt-in evidence-grounded extraction path; it has **not** established reliable natural-language understanding across every industry or model.

## Why another memory project?

| Most memory demos emphasize… | This project focuses on… |
| --- | --- |
| Save and search text | **Current canonical state + preserved historical versions** |
| Top-k semantic matches | **Evidence-bound recall + tenant/time eligibility checks** |
| Overwriting a stale fact | **Explicit UPSERT / RETRACT with expected claim ID and value** |
| One provider or one application | **OpenAI-compatible / Ollama adapters and domain-neutral fact candidates** |
| One retrieval index | **PostgreSQL FTS + pgvector + Apache AGE as complementary projections** |
| Success-path examples | **Outbox, idempotency, Temporal verification activities and failure-focused tests** |

This is **not** a replacement for LangGraph, AgentScope, Temporal or the model you already use. It is an independent memory component an agent can call through a Python SDK or REST/OpenAPI.

## Quickstart

**Prerequisites:** Python 3.10+, Docker Compose v2. These commands run a **local development stack**; they do not start a production SaaS deployment.

```bash
git clone https://github.com/hippoley/Agent-Memory-Core-v0.1.0.git
cd Agent-Memory-Core-v0.1.0
python scripts/launch_local.py up --profile lite
```

Explore [Console](http://127.0.0.1:8765/dashboard/), [Swagger](http://127.0.0.1:8765/docs/swagger), or [OpenAPI](http://127.0.0.1:8765/openapi.json). A random local API key is written to the ignored `.env.local`. **Never expose the development database/API directly to the Internet.**

```bash
# Base memory + API
python -m pip install -e '.[api,postgres]'

# Optional Temporal workflows; run a separate Temporal server when needed
python -m pip install -e '.[temporal]'
```

### A real SDK call, not pseudocode

```python
from datetime import datetime, timezone
from agent_memory_core.sdk import MemoryClient

with MemoryClient("http://127.0.0.1:8765", "YOUR_LOCAL_API_KEY") as memory:
    memory.remember(
        subject_id="supplier-a",
        predicate="settlement_period",
        value={"months": 1},
        source_id="contract:version-1",
        valid_from=datetime.now(timezone.utc).isoformat(),
        idempotency_key="contract-v1:settlement",
    )
    print(memory.recall("supplier-a"))
```

For structured correction and provenance, run [`examples/belief_revision.py`](examples/belief_revision.py). For model-proposed corrections with **retrieved canonical evidence**, see [evidence-grounded revision](docs/EVIDENCE-GROUNDED-REVISION.md).

**Important:** Lite currently still runs the combined PostgreSQL database image; it disables some retrieval channels but is **not** SQLite-only, extension-free or a measured low-RAM claim. [Exact profile limitations](docs/LIGHTWEIGHT-RUNTIME.md).

## Architecture

![Memory runtime topology — API, deterministic processor, canonical PostgreSQL, pgvector, Apache AGE and optional adapters](docs/assets/architecture.svg)

![Memory lifecycle — proposal, canonical revisions, derived indexes and canonical recheck](docs/assets/memory-lifecycle.svg)

**What this does not yet guarantee:** universal tenant authorization in every retrieval channel, independently verified evidence truth, production-grade reliability, or uniformly correct LLM interpretation. See the [architecture review](docs/ARCHITECTURE-REVIEW.md).

```text
Any agent / SDK / REST
         |
   Model-adapter boundary  <---- OpenAI-compatible LLM / Ollama
         |
   Candidate extraction + evidence-grounded retrieval
         |
   Deterministic revision guard (claim ID, old value, tenant, time)
         |
   PostgreSQL — canonical claims, provenance, bitemporal history, outbox
         |                         |                       |
   FTS / pgvector             Apache AGE           Temporal adapter
   lexical + semantic          graph projection    durable verification
         \_________________________/
                   |
     Canonical-filtered hybrid recall
                   |
           Agent / context consumer
```

The core invariant: **one canonical authority, multiple retrieval views**. PostgreSQL owns committed claims and revision history. Vectors and graph edges can be rebuilt, and a high embedding score never overrides the currently eligible record.

- **Write:** capture source → model/rule candidates → identity and revision checks → transactional canonical write → recoverable projections.
- **Recall:** lexical/vector/graph candidates → fusion → canonical tenant/bitemporal filter → source-addressable results.
- **Revision:** optional `ground_with_memory` retrieves up to eight current canonical claims and passes them to the configured model as evidence. RETRACT proposals must cite the matching claim ID and old value; PostgreSQL rechecks both.
- **Execution:** outbox and workers handle repeatable downstream delivery; Temporal is available for durable, multi-step verification.

[Architecture review](docs/ARCHITECTURE-REVIEW.md) · [Deep dive (中文)](docs/ARCHITECTURE-DEEP-DIVE.zh-CN.md) · [Source → evidence → revision](docs/EVIDENCE-GROUNDED-REVISION.md)

## First-class extensions: Apache AGE and Temporal

**We deliberately keep both.** “Lightweight” here means **one coherent memory runtime and selectable deployment paths**, not abandoning temporal graph reasoning or durable execution.

| Extension | What it adds | Current boundary |
| --- | --- | --- |
| **Apache AGE** | Entity/relationship projections and graph-neighborhood candidates; combined with SQL/FTS/vector retrieval | PostgreSQL extension with an optional graph channel; the current server **disables AGE under principal-bound RLS mode** pending security validation |
| **Temporal** | Durable workflow history, Activities, retries and recoverable verification receipts | `VerifyMemoryWorkflow` plus PostgreSQL-backed Activities exists; **not** every ingestion or revision operation is a Temporal workflow |
| **pgvector** | Semantic nearest-neighbor candidate retrieval | Requires an actual, compatible embedding provider and enabled vector retrieval |
| **Outbox** | Projection and external delivery recovery | Core PostgreSQL event delivery; no Redis/Kafka requirement |

**Extension architecture goals:** evidence-aware graph revisions, consistent source-to-edge lineage, durable reindex/reverification and fault-injection benchmarks. See [AGE + Temporal roadmap](docs/AGE-TEMPORAL-STRATEGY.md) for tested reality versus planned work.

### Deployment profiles

| Profile | Canonical store | Vector / AGE query flags | Projection worker | Temporal |
| --- | --- | --- | --- | --- |
| Lite | PostgreSQL | Off | No | Not started |
| Standard | PostgreSQL | On (providers required) | No | Not started |
| Full | PostgreSQL | On (providers required) | Yes | Separate optional service |

Switch profiles with `python scripts/launch_local.py up --profile standard` or `--profile full`. Full **does not** automatically deploy Temporal. For the actual local setup and limitations see [the runtime guide](docs/LIGHTWEIGHT-RUNTIME.md).

## Evidence, not inflated benchmarks

The repo preserves [200 fixed Chinese extraction cases](data/eval/gold_home_zh.jsonl), [12 adversarial multi-turn stories](data/eval/zh_multiturn_frozen_v1.jsonl), and a [four-stage strict scorer](scripts/eval/score_zh_frozen.py).

The latest [real 200-case rule-extractor replay](data/eval/ZH-200-RULE-REPLAY-20261116.md) measured **94.34% extraction F1** on its in-scope A/B/C examples — **not** memory revision F1, not real-model quality and not an end-to-end answer score. Out-of-vocabulary exact coverage was **0/30**. That weakness is one reason the model-agnostic, evidence-grounded path matters.

**Still open:** genuine local-model cross-domain scoring, accurate compound retrieval on a frozen corpus, multi-operation all-or-nothing revision, hostile multi-tenant security assessment and measured Lite resource advantage.

```bash
python -m pip install -e '.[dev,postgres,api]'
pytest -q
# With a disposable, real PostgreSQL / pgvector / AGE test stack:
export MEMORY_TEST_DSN='postgresql://memory:local-dev-only@127.0.0.1:55432/memory'
pytest -q -m integration
```

See the [User Story acceptance matrix](docs/USER-STORY-ACCEPTANCE.md) for evidence and remaining release gates. CI green is not a substitute for model accuracy or production authorization.

## Help build the missing layer between RAG and reliable agents

The best contributions are **reproducible failures with evidence**:

- A correction that leaves the wrong fact active; a stale or cross-tenant graph edge; an ambiguous model-generated retraction.
- An AGE temporal-relationship or RLS safety test that fails, with a minimal dataset.
- A Temporal crash/retry case that duplicates a receipt or loses progress.
- A benchmark comparing real embedding providers or token/latency costs on a frozen dataset.
- A small compatible agent-framework adapter, or a clear API/documentation improvement.

We prioritize bugs and tests that make other developers safer before adding feature count. See [CONTRIBUTING](CONTRIBUTING.md), [SECURITY](SECURITY.md), [community launch kit](docs/COMMUNITY-LAUNCH.md), and [engineering roadmap](docs/AGE-TEMPORAL-STRATEGY.md).

### Project status

**Apache-2.0 · Independent open-source research prototype · Python 3.10+**

This project has real runnable code and PostgreSQL integration tests. It is **not yet** a validated zero-trust multi-tenant SaaS, a universal fact checker, or a guaranteed correct cross-industry LLM memory engine. Don't put production secrets or regulated data into its demo instance.

---

<div align="center">

**Memory shouldn't just survive a session. It should survive being wrong.**

[中文导览](docs/START-HERE.zh-CN.md) · [Interactive Atlas](docs/demo/index.html) · [API / Swagger guide](docs/SWAGGER_GUIDE.zh-CN.md) · [Report a reproducible issue](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/issues)

</div>
