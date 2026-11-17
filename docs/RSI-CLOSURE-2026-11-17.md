# Agent-Memory-Core · RSI closure evidence matrix (2026-11-17)

Baseline: `main` at `f58221c2f26b6c82c77b10b7235eb97a04cc2ff7` before this branch. This document is an evidence ledger, **not** a claim that all stories passed. Never replace a failure/skip with "verified". For each gate, record the exact commit, command, runtime versions, completed test counts, saved artifact paths and deviations.

## P0-A · Memory Observatory, true browser acceptance

Existing executable tests (already on main):
- `tests/test_memory_observatory_browser.py`: real Chromium with mocked API; five-step demo, keyboard/focus, search/replay/export, synthetic/empty/500-record pages, XSS text rendering, HTTP/network failures and 390/768/1440/1920 viewports.
- `tests/test_observatory_browser_live_postgres.py`: **no mocked routes**; actual Uvicorn HTTP and canonical PostgreSQL writes, revision, SQL verification, tenant isolation, 401/403, reload and an injected connection error (not physical DB crash).
- `.github/workflows/observatory-browser-acceptance.yml`: isolated PostgreSQL + Playwright execution, screenshot artifact upload and database teardown.

Acceptance, not merely source coverage:
1. Run on a disposable database from a fixed SHA; keep workflow URL and complete job result (pass/fail/skip). Do not count ordinary unit CI as Chromium acceptance.
2. Save screenshots at widths 390, 768, 1440, 1920; review actual text clipping, contrast and focus order manually. A zero-overflow assertion cannot certify visual quality.
3. Inspect console errors, request failures, JSON export body and accessibility (keyboard navigation, dialog focus trap, labels, reduced motion, screen reader audit). Current suites cover *some*, not all, of these.
4. Demonstrate actual same-origin Live credentials on a disposable tenant and confirm synthetic vs live distinction. **Never** send tenant tokens to public Pages.
5. Separate a successful browser test from a successfully deployed and manually opened public GitHub Pages URL. The latter is currently not verified.

Local run (requires Docker, Python environment and Chromium; these instructions **do not** claim execution):

```bash
docker compose up -d --build --wait
python -m pip install -e '.[dev,api,postgres]' 'playwright>=1.45'
python -m playwright install chromium
MEMORY_TEST_DSN='postgresql://memory:local-dev-only@127.0.0.1:55432/memory' \
MEMORY_OBSERVATORY_BROWSER=1 MEMORY_OBSERVATORY_LIVE_BROWSER=1 \
MEMORY_OBSERVATORY_SCREENSHOT_DIR=artifacts/observatory \
python -m pytest -q tests/test_memory_observatory_browser.py tests/test_observatory_browser_live_postgres.py
docker compose down -v
```

Status: **implemented tests; exact-main verified browser-run evidence still required**.

## P0-B · Real LLM memory correctness

Already present: opt-in evidence-grounded revision (PR #27), model adapters, canonical ID guard, strict batch transaction (PR #33) and rule-only Chinese evaluation. None establishes real-model quality.

Frozen end-to-end cases must include at least Chinese + cross-domain supplier, home-device, personal preference and temporal policies, with:
- baseline before correction; repeated or contradictory user turn; resulting canonical claim IDs and source evidence; cross-session question; model answer and adjudicated gold;
- negative/null cases, entity ambiguity, coreference, negation, stale-ID, multi-hop, unretrievable evidence and competing-tenancy cases;
- exact model, quantization, prompt template, generation options, embedding model, corpus SHA, DB snapshot and retrieval settings.
Publish per-stage extraction, withdrawal/upsert, Recall@k, MRR/nDCG, answer factual accuracy, unsupported-answer rate, p50/p95 latency, CPU/memory and failures. Score a rule baseline separately from real model. Fail gate on ungrounded retract or old fact asserted as current, regardless of aggregate average.

Status: **unverified in real-model end-to-end evaluation**.

## P1-A · Bitemporal interval and historical revision

Existing S07 three-version PostgreSQL test covers a narrower contract. New acceptance:
1. distinguish `valid_time` (effective in the world) from `recorded_time` (known by the system);
2. insert late evidence and query before/after recording while fixing a past valid interval;
3. revise overlapping vs adjacent intervals, backdate a compound correction, replay `as_of` at both axes;
4. concurrent conflicting corrections must serialize or fail explicitly, with unchanged provenance and outbox on failure.

The strict compound API currently **rejects historical observed_at**; do not claim historical compound replay.

Status: **partial**.

## P1-B · Security, recovery and graph consistency

Known: principal-bound RLS test, projection/rebuild test, Temporal receipt integration, and outbox/worker fault tests. Required additional independent evidence:
- unauthorized same-tenant principal, tenant-to-tenant traversal, revoked credential, key rotation, direct SQL bypass attempts; prohibit AGE path from bypassing restricted RLS;
- real PostgreSQL process kill/restart and restored backup; verify active claims, audit, outbox, projections, no double-applied commands;
- real Temporal worker kill/restart and workflow replay; distinguish Temporal dev server persistence from production durability;
- AGE projection corruption/delete/rebuild against canonical SQL, with before/after content reconciliation and no leaked records;
- unavailable embedding and object backend, delayed delivery, object GC and external storage restart;
- TLS/rate limit/retention/erasure validation in target deployment.

Status: **production gate not passed**. An injected store factory exception is not a physical database restart.

## P2 · Independent third-party adoption and extensibility

Acceptance requires a separate repository and a clean environment, not running repository-internal examples:
- install a tagged wheel / SDK, discover capabilities, authenticate and call memory create/revise/retract/search/events;
- verify request/response compatibility with an old supported client, durable webhook delivery and error contracts;
- validate external S3-compatible object adapter and complete time-series partition/retention lifecycle independently;
- record 100+ agent concurrency correctness, p50/p95/p99, worker resource and failure recovery under controlled load;
- execute setup from published docs without author assistance and keep a reproducible receipt.

Status: **not independently qualified**.

## Integration and merge rules

- Keep PR #16/#17 unmerged until a path-by-path comparison establishes which code is already on main; their stale branch diffs are **not** unique-feature proof.
- Develop on a fresh branch, run targeted tests locally first, and only run complete GitHub CI after the batch is coherent. A test being present is not a test passing.
- Leave `main` unchanged until same-head checks and review pass. No production secrets, resources or tenant data in artifacts.
- Promote each gate only when a human-readable evidence record links a reproducible completed run and required artifacts. Unknown remains unknown.
