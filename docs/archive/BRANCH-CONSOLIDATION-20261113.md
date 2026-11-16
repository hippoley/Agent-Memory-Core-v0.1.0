# Branch consolidation audit — 2026-11-13

## Integration decision

**New integration branch:** `integrate/agent-memory-core-unified-20261113`  
**Frozen starting main SHA:** `643b6de07ad77a2bebd5d8a95ce0ca67ef449b39`

This is an **integration workspace**, not an assertion that every historical branch has been blindly merged. All source code and tests already on that latest main are retained. PR #16 documentation/demo files and PR #17 local runtime/benchmark files are selectively copied. The two conflicting shared files, `README.md` and `.gitignore`, are manually reconciled, not overwritten with older versions.

## Integrated now

| Source | Included | How |
|---|---|---|
| `main` (as above) | All canonical files, including LongMemEval-24 and the latest weighted retrieval fusion | Branch parent; preserved by Git history |
| PR #16 `feat/public-demo-and-architecture-docs` | Brand/README design, original architecture SVGs, interactive demo, deep dive, audit, Chinese guide, contributor/security guidance, API example and associated tests | Copy compatible nonexisting files; hand-merge README |
| PR #17 `feat/lite-one-command-benchmarks` | Local Compose API/worker overlays, launcher, Dockerfile, resource measurement, acceptance tests/docs/workflow | Copy compatible nonexisting files; merge .gitignore |
| Historical main engineering README | Latest main content retained as `docs/REFERENCE-README.md` | No instructions discarded |

## Existing historical branches — do not brute-force merge

All names below were present on the remote during the audit. `ahead` and `behind` counts were obtained by comparing to `main` before creating this integration branch. A branch being ahead does **not** mean its implementation should supersede newer main.

| Branch | Ahead / Behind main | Disposition |
|---|---:|---|
| `feat/complete-memory-api-v1` | 2 / 102 | Legacy API/schema implementation; inspect specific deltas before adoption |
| `feat/database-rls-tenant-isolation` | 2 / 40 | Security-specific changes; verify against current principal-bound RLS |
| `feat/durable-outbox-projection-fanout` | 52 / 43 | Diverged pipeline; manual semantic diff only; do not replace current projections |
| `feat/durable-webhook-delivery` | 31 / 42 | Diverged webhook changes; verify duplication/current fixes |
| `feat/reliable-projection-engine` | 7 / 43 | Potential old projection code; do not replace newer canonical worker |
| `feat/s05-graph-rebuild-acceptance` | 1 / 37 | Narrow graph test; check if scenario is still untested on main |
| `feat/s09-worker-crash-fencing` | 1 / 35 | Crash-fencing regression to compare with current behavior |
| `feat/s10-principal-bound-rls` | 1 / 36 | RLS branch; review security details against current migration |
| `feat/s10-rls-enforcement` | 0 / 36 | Fully behind main; no unique ahead commits |
| `feat/user-story-closure-rsi` | 38 / 41 | Wide old branch; current main implements portions; audit per acceptance gate |
| `fix/memory-association-cardinality-20261110` | 77 / 43 | Wide diverged cardinality branch; must not overwrite newer policy logic |
| `integrate/main-unique-features-and-cardinality` | 5 / 29 | Earlier integration attempt; inspect for truly missing independent tests |
| `test/s03-atomic-rollback-fault-injection` | 1 / 38 | Narrow failure test; compare against current S03 coverage |
| `test/s07-bitemporal-acceptance` | 1 / 40 | Narrow temporal test; compare against current S07 coverage |
| `test/s08-real-tls-receiver` | 1 / 34 | TLS receiver test; compare against current S08 coverage |
| `docs/correct-branch-inventory-audit` | 1 / 28 | Older inventory correction; retain current branch audit separately |
| `fix/s01-sdk-error-contract` | 3 / 39 | Older SDK error handling; compare against current `sdk.py` behavior |

The old branches remain intact for provenance. They are **not deleted**. The wide divergent branches cannot be treated as a safe addition of code volume: schema and RLS changes have compatibility and behavioral consequences.

## Reconciliation guarantees

1. **Never downgrade main's retrieval:** weighted channel fusion, newer FTS/query semantics and evaluation scripts stay on main lineage.
2. **No duplicate migration authority:** the consolidated branch does not import an older variant of `001_core.sql` or `postgres.py`.
3. **No secrets in Git:** generated `.env.local` and resource reports are ignored.
4. **No fabricated benchmark:** only actual CI/local runner artifacts qualify as observations; do not compare synthetic demo values to production.
5. **One integration PR:** target `main`, with same-SHA correctness tests, real Docker profile startup and an explicit merge decision.
6. **Historical code is not discarded:** all original branch names remain and can be examined later; inclusion is based on useful, compatible behavior, not raw file count.

## Still open

- [ ] Validate same-head integration CI and repair any runtime incompatibility with latest main.
- [ ] Validate full-profile Docker worker liveness and all three profile resource reports.
- [ ] Run static demo / README link and SVG parsing tests against the merged branch.
- [ ] Review old branches' narrow independent safety tests for missing scenarios; transplant only when they still increase coverage.
- [ ] Merge the integrated PR into `main` when checks have passed.

This document intentionally distinguishes **physical integration already committed** from **historical feature review still outstanding**.
