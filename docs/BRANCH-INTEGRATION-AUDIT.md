# Main-branch consolidation audit

Audit scope: repository `hippoley/Agent-Memory-Core-v0.1.0` only.
Baseline: default branch `main` at `10cc9ba6b575a3a7e76299c31d7abe82e7badad7`.
The goal is a complete, coherent main branch, not a zero-branch count. Original
branches and PRs are retained for provenance; no force-push or branch deletion
is part of this consolidation.

## Inventory

At audit time GitHub exposed 15 branches including `main`, and two open Draft
PRs (#1 and #2). The following dispositions were made by comparing actual
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
8. A 14-case Chinese smoke corpus, explicitly distinguished from the
   200+ human-adjudicated release-quality corpus.
9. Current `sdk.py` documentation and polarity-aware extraction evaluation.

## Deliberately not copied

Old branch files are not valuable merely because they are absent by path.
Duplicate SDK implementations, a second FTS schema, a second projection-job
ledger, old worker handlers and benchmark scripts bound to those old schemas
would create two competing implementations. Their intended functionality is
either present in current main or replaced by the ported current implementation.
The old branch and PR remain available as historical source.

## Completion rule

A branch is considered reconciled when every unique capability has one of:
- an implementation and tests on main;
- an explicit superseding implementation on main; or
- a documented reason not to merge (security defect, stale schema or duplicate).

Branch deletion is not required. A clean main branch is the deliverable; the
historical branches remain evidence and rollback references.
