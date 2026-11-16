# Main-branch consolidation audit

Audit scope: repository `hippoley/Agent-Memory-Core-v0.1.0` only.
Baseline at the time of consolidation: `main` at `10cc9ba6b575a3a7e76299c31d7abe82e7badad7`.
Re-verified on 2026-11-12 against `main` at `3d5415cf62300e36e21890b8ead0b25b9caf1b36`;
that re-verification is reproducible, see "Re-verification" below.
Last synced with the remote on 2026-11-12 against `main` at
`ff13dc4` — local and `origin/main` point at the same commit.
The goal is a complete, coherent main branch, not a zero-branch count. Original
branches and PRs are retained for provenance; no force-push or branch deletion
is part of this consolidation.

## Inventory

At initial audit GitHub exposed 16 branches including `main`, and two open Draft
PRs (#1 and #2). After creating and retaining this consolidation branch, the
repository currently has 18 branches including `main` (the retained consolidation and audit branches are included); PR #14 is merged and
PRs #1 and #2 are closed as superseded. The following dispositions were made by comparing actual
Git trees and code, not by merging branch names blindly.

| Branch / PR | Main-branch disposition |
|---|---|
| `feat/durable-outbox-projection-fanout` / PR #3 | Already merged. All branch paths exist on main; main has subsequent fixes. |
| `feat/durable-webhook-delivery` / PR #4 | Already merged. All branch paths exist on main; main has subsequent S08/S09/S10 fixes. |
| `feat/user-story-closure-rsi` / PR #5 | Already merged. Current main contains its evaluation runners, concurrency tests, outbox recovery and docs. |
| `test/s07-bitemporal-acceptance` / PR #6 | Already merged; test is on main. |
| `fix/s01-sdk-error-contract` / PR #7 | Already merged; current SDK contains its error contract and safe path segments. |
| `test/s03-atomic-rollback-fault-injection` / PR #8 | Already merged; test is on main. |
| `feat/s05-graph-rebuild-acceptance` / PR #9 | Already merged; graph rebuild and tests are on main. |
| `feat/s10-principal-bound-rls` / PR #11 | Already merged; principal-bound RLS is on main. |
| `feat/s09-worker-crash-fencing` / PR #12 | Already merged; lease fencing and SIGKILL test are on main. |
| `test/s08-real-tls-receiver` / PR #13 | Already merged; real TLS receiver test is on main. |
| `feat/s10-rls-enforcement` and `feat/database-rls-tenant-isolation` | Not merged. Earlier mutable-session-GUC RLS design is unsafe; replaced by principal-bound `session_user` mapping. |
| `feat/complete-memory-api-v1` | Not merged. Its object registry uses the old `sha256/size_bytes` schema and non-autocommit connection behavior; current main has the later `object_sha256/byte_size` schema, autocommit transaction discipline and richer APIs. |
| PR #1 / `feat/reliable-projection-engine` | Not merged wholesale. Its `client.py`, `fts.py`, `projection_jobs.py` and `vector_projection.py` use an older client/projection schema. Current main uses `sdk.py`, `projections.py`, `memory_claim_search` and channel-specific projection deliveries. Useful SDK documentation and a smoke corpus are rewritten for current interfaces; stale duplicate modules are not copied. |
| PR #2 / `fix/memory-association-cardinality-20261110` | Not merged wholesale. Its cardinality model is ported into current relation-aware processing and PostgreSQL transaction code. Its old `embedding_projection.py` worker handler is superseded by `PostgresProjectionHandlers.vector`; the useful offline local model loader and 100-Agent benchmark are ported. |
| `integrate/main-unique-features-and-cardinality` | Superseded. Its five commits are an earlier consolidation attempt; every path it added already exists on `main`, and current `main` carries the later predicate-policy endpoints and extractor-neutrality work that the branch predates. |
| `docs/correct-branch-inventory-audit` | Superseded. It only edited this document, with counts that the dashboard/browse work and the subsequent cleanup made stale; this file is the newer version. |

## Ported into the current architecture

1. Trusted tenant-scoped predicate cardinality (`one` / `many`) with a new
   ordered migration mirrored in source SQL and wheel resources.
2. Consistent advisory-lock order: predicate policy first, then fact key.
   Policy changes serialize against writes; existing predicate history cannot
   be silently reinterpreted.
3. Direct writes enforce non-overlapping single-valued facts. Multi-valued
   facts coexist; exact duplicates link provenance without another event.
4. The model cannot choose cardinality. A supplied cardinality inconsistent
   with the database policy is rejected. Unresolved or contradictory
   `subject_name` resolution fails closed.
5. RLS includes the predicate-policy table. Tenant logins may read their own
   policy but cannot mutate trusted policy rows; administration is separate.
6. Local SentenceTransformer loading requires an existing local directory,
   disables remote code and sets `local_files_only=True`.
7. A reproducible 100-logical-Agent PostgreSQL same-fact benchmark.
8. A 12-case Chinese smoke corpus, explicitly distinguished from the
   200+ human-adjudicated release-quality corpus.
9. Current `sdk.py` documentation and polarity-aware extraction evaluation.

## Deliberately not copied

Old branch files are not valuable merely because they are absent by path.
Duplicate SDK implementations, a second FTS schema, a second projection-job
ledger, old worker handlers and benchmark scripts bound to those old schemas
would create two competing implementations. Their intended functionality is
either present in current main or replaced by the ported current implementation.
The old branch and PR remain available as historical source.

## Re-verification (2026-11-12)

The consolidation above was re-checked against the current `main`, because a
claim like "main already contains everything valuable" decays the moment either
side moves. The check is a path-level set difference, never a commit count: a
branch can be dozens of commits ahead and still contain nothing `main` lacks.

```bash
python scripts/audit_branches.py            # table
python scripts/audit_branches.py --json     # machine readable
```

For every remote branch the script takes its merge base with `main`, lists the
paths the branch *added*, and reports those absent at the same path on `main`
(`DRIFT`). `DRIFT` is a question, not a verdict — the capability may live on
`main` under another name — so each one is resolved below by reading code.

Result at `main @ ff13dc4` (18 refs including `main`; 17 branches audited):

- **14 branches `COVERED`** — every path they added exists on `main`.
- **3 branches `DRIFT`**, 15 paths total (below). No drift path is a missing
  capability; each is a stale duplicate superseded by a current implementation.

| Branch | Paths absent on `main` | Disposition |
|---|---|---|
| `feat/reliable-projection-engine` (PR #1, closed) | `client.py`, `fts.py`, `projection_jobs.py`, `vector_projection.py`, `benchmarking.py`, `benchmarks/MEMORY-PIPELINE.md`, `benchmarks/chinese_memory_eval.jsonl`, `scripts/benchmark_memory_pipeline.py`, `tests/test_client.py`, `tests/test_evaluation.py`, `tests/test_projection_worker_cli.py` — 11 | Superseded. Live equivalents: `sdk.py`; `memory_claim_search` in `postgres.py`; `projections.py` (`PostgresProjectionHandlers.vector`); `scripts/benchmark_memory_concurrency.py` + `scripts/benchmark_http.py`; `evaluation.py` + `scripts/evaluate_local_extractor.py` / `evaluate_retrieval.py`. Its corpus holds the same 12 scenarios in an older schema (`expected`, no `category`); `main` keeps the categorised form that the evaluator validates. |
| `fix/memory-association-cardinality-20261110` (PR #2, closed) | `embedding_projection.py`, `tests/test_vector_outbox_runtime.py` — 2 | Superseded. `local_sentence_transformer` lives in `local_embeddings.py`; the worker handler is `PostgresProjectionHandlers.vector`; outbox coverage is `tests/test_outbox_runtime.py` + `tests/test_outbox_api_integration.py`. |
| `feat/database-rls-tenant-isolation` | `sql/postgresql/004_tenant_rls.sql`, `src/agent_memory_core/resources/004_tenant_rls.sql` — 2 | Rejected, not missing. The mutable-session-GUC RLS design was replaced by the principal-bound one (PR #11); the path also collides with the shipped `004_predicate_cardinality.sql`. |

PR snapshot at the same moment: 14 PRs total, all closed — 12 merged (#3–#9,
#11–#15), 2 closed as superseded (#1, #2). No open PR. Branch and PR references
are kept for provenance.

Two corrections to earlier reporting, recorded so the numbers are not repeated:

- The latest `main` is **not** `ecae810`; that was the tip before the dashboard,
  browse/policy endpoints and extractor-neutrality work landed. Current tip is
  `ff13dc4`.
- The smoke corpus is **12 cases**, not 14 (`benchmarks/README.md` was already
  right; item 8 above was wrong and is fixed).

## Completion rule

A branch is considered reconciled when every unique capability has one of:
- an implementation and tests on main;
- an explicit superseding implementation on main; or
- a documented reason not to merge (security defect, stale schema or duplicate).

Branch deletion is not required. A clean main branch is the deliverable; the
historical branches remain evidence and rollback references.
