# Contributing to Agent Memory Core

Thank you for considering a contribution. The aim is not to grow the number of memory features; it is to make **state, evidence, time and failure behavior independently testable**.

## Where contributions have the greatest impact

1. **Correctness:** bitemporal interval edge cases; concurrent replacement; provenance and trusted cardinality; stale graph/vector projection rejection.
2. **Reproducibility:** pinned PostgreSQL/AGE/pgvector versions; crash-and-replay test matrices; runnable small-host memory/latency benchmarks.
3. **Recall quality:** judged multilingual datasets, failure taxonomy, cross-source contradictions, calibrated candidate extraction. Never publish invented evaluation scores.
4. **Integration:** independently reproducible SDK examples, OpenAPI compatibility checks, model-agnostic extractor adapters.
5. **Security:** tenant-bound authorization, database RLS, secret redaction and isolation tests.

## Development

```bash
python -m pip install -e '.[dev,api,postgres]'
pytest -q
```

For PostgreSQL integration, start the test Compose stack (see [README](README.md)), set `MEMORY_TEST_DSN`, then run `pytest -q -m integration`.

## Pull request standard

- Open a focused issue or PR with the **User Story**, actual behavior, expected behavior and an independently repeatable test.
- Include migrations and rollback considerations for state/schema changes.
- Include a regression test for correctness fixes. If integration depends on a running database or model, state what was run and what was not.
- Do not make an existing claim appear “verified” based on a simulated model or fixture.
- Keep source provenance, temporal validity, idempotency and tenant constraints visible in code review.
- Include logs without secrets, private records or raw third-party conversations.
- Do not introduce mandatory infrastructure sidecars without a measured reason.

## Reporting security vulnerabilities

Do **not** publish an exploit or secret in a public issue. Follow [SECURITY.md](SECURITY.md).

## Architecture before optimization

PostgreSQL remains canonical. Vector and AGE are derived views. Optional workers must not be required for basic canonical read/write. New dependencies should be justified with measured cost and reproducible evidence.

For protocol or semantics proposals, start with a short ADR: problem → invariants → alternative designs → acceptance tests → migration impact.
