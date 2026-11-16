# Community launch kit — a credible, high-signal first impression

**Project:** Agent Memory Core · Apache-2.0 · research prototype

We want a newcomer to understand the problem in **15 seconds**, run it in **5 minutes**, identify the distinct engineering bet in **2 minutes**, and see how to contribute a reproducible improvement. No vague “world-class” claims, fake user counts, manufactured benchmarks or comparisons to unmeasured competitors.

## GitHub About — ready to paste

**Repository description (recommended, under 160 characters):**

> Lightweight agent memory runtime with evidence-grounded belief revision, bitemporal recall, pgvector, Apache AGE graphs & Temporal workflows.

**Short alternative:**

> Agents change their minds. Memory should keep the evidence. Open-source agent memory with pgvector, Apache AGE & Temporal.

**Website (only if a tested public demo exists):** Leave unset for now, or use the GitHub-hosted README/demo path. The local console at 127.0.0.1 is **not** a website for remote visitors.

**Topics (suggested, GitHub maximum 20):**

`agent-memory`, `ai-agents`, `llm`, `rag`, `memory`, `postgresql`, `pgvector`, `apache-age`, `temporal`, `knowledge-graph`, `bitemporal`, `vector-search`, `python`, `openai-compatible`, `local-first`, `open-source`, `agent-infrastructure`.

**Access:** GitHub repository About metadata is not modified by these source commits. A repo admin must update Description, Website and Topics in the repository's **About → Edit** UI. These values are proposed, not asserted as currently live.

## The 3-line pitch

1. **Problem:** Agents can remember contradictory things without knowing which version is authoritative.
2. **Mechanism:** LLM proposals + deterministic revisions + evidence/temporal history; pgvector and Apache AGE discover candidates, Temporal runs durable verification.
3. **Result to verify:** Developers can build agents that explain, correct and replay their memory state rather than blindly inject the nearest vector.

## Contributor on-ramps that create visible third-party value

| Track | First task | Evidence that closes it |
| --- | --- | --- |
| Memory correctness | Reproduce stale claim referenced after a revision | Frozen failing test + fix + historical/current assertions |
| Temporal graph | Show a graph edge that outlives its superseded claim | Event + graph projection + SQL canonical cross-check |
| Durable execution | Kill/restart Temporal verification Activity | Same logical receipt/event ID, no unintended repeats |
| Model portability | Test Qwen/other real provider on same frozen cases | Config SHA, anonymized predictions, failures, costs |
| Lightweight operations | Compare Lite/Standard/Full on one machine | Reproducible CPU/RAM, requests, P95 and data-size report |
| API integrations | Build minimal LangGraph/AgentScope adapter | Public 10-line example + complete User Story test |

### Good issue title examples

- `bug(revision): new claim commits after old claim rollback`
- `test(age): graph traversal returns superseded relation`
- `test(temporal): verification replay should not duplicate receipt`
- `benchmark(embedding): compare exact fact Recall@5 on identical corpus`
- `docs: first-run setup fails on fresh Python environment`

A useful issue includes the exact commit SHA, relevant API inputs, expected vs observed behavior, environment, a reproducible command and safe redacted logs.

## Launch editorial principles

- Show the broken agent behavior before the architectural components.
- Highlight **two real distinguishing systems**: AGE relationships and Temporal verification, both bound to PostgreSQL truth.
- Place a runnable installation snippet before the deep dive.
- Distinguish **implemented**, **partially exercised**, and **future work**.
- Avoid claiming “production-ready”, “best”, “universal 90% accuracy”, “zero infrastructure”, or “exactly-once Temporal effects”.
- Never cite the 94.34% fixed-rule extraction F1 as an end-to-end or general-model accuracy number.
- Do not expose localhost screenshots as hosted interactive demos; the static Memory Atlas is a walkthrough.
- Prefer independent integrations, reproducible failure cases and externally re-runnable benchmarks over raw Star count.

## Recommended release narrative

**v0.1.x — Reliable belief state:** canonical revision, bitemporal history, evidence, API, real DB tests.

**v0.2 — Graph-aware memory:** explicit temporal AGE projection semantics, tenant-safe query gates, before/after graph recall benchmark.

**v0.3 — Durable verification:** Temporal-backed provenance verification and reindex workflows with crash/replay tests.

**v0.4 — Portable agent adoption:** SDK adapters and prepared-context contract, cross-domain real-model and resource benchmark suite.

These versions are proposed milestones, **not released versions or delivery commitments**. Any milestone should be declared complete only after its exact User Stories are tested and reproducible.

## User-facing copy for a first post

> Most agents can remember a preference. Far fewer can explain why an old preference stopped being true.
>
> Agent Memory Core is an open-source experiment in evidence-grounded belief revision: PostgreSQL keeps the history, pgvector and Apache AGE find relationships, and Temporal supports durable verification when jobs outlive a single request.
>
> It's early. The hard parts — cross-domain model quality, atomic compound revisions, graph tenant security and production operations — are visible in the repo, not hidden behind a 'production-ready' badge.
>
> If you've seen an agent retrieve a stale memory and make the wrong decision, we'd love a reproducible example.

This is a proposed announcement, not evidence that any announcement was posted.

## Pre-release checklist (consolidated from the former LAUNCH-KIT)

The following checks are *not* evidence of a completed public release.

- [ ] Re-clone without local environment files and run Quickstart from `main`.
- [ ] Verify an authenticated remember → revision → recall scenario using Swagger and SDK.
- [ ] Check README relative links, architecture SVGs in light/dark mode, and actual GitHub-hosted demo URLs.
- [ ] Export a social-preview PNG from `docs/assets/hero-belief-lifecycle.svg`; do not present a synthetic demo as a real recorded run.
- [ ] Check demo files and screenshots for tokens, passwords and private memory records.
- [ ] Publish exact commit SHA, hardware, dataset and reproducible commands with any benchmark claims.
- [ ] Update GitHub About / Topics / social preview manually after verifying their public URLs.
- [ ] Tag a prerelease only after its specific User Story gates pass.
- [ ] Enable Discussions only if maintainers can realistically respond.

Prefer reproducible counterexamples over hollow Star counts.
