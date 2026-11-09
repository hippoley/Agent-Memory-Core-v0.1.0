# Milestone: PostgreSQL triple-store prototype

Date: 2026-11-09
Repository: hippoley/Agent-Memory-Core-v0.1.0
Verified main SHA: afb707e4eddb7f114219d84daaeec84936dc03a1

## Done

- PostgreSQL: sourced claims, system time + valid time, corrections, observations.
- pgvector: exact tenant/time-filtered search; both 384D and 512D model profiles.
- Apache AGE: actual Cypher one/two-hop relationships, SQL ledger, atomic writes,
  retraction reconciliation for superseded claims or retired edges.
- Hybrid: SQL keywords, vector and AGE with canonical tenant/temporal rechecking.
- Package: wheel contains PostgreSQL migration SQL, plus test of contents.
- Offline: no dependency on hosted LLM or embedding APIs in core storage.

## Verified evidence

GitHub Actions after the last source code change afb707e:
- Unit tests and package wheel: SUCCESS.
- PostgreSQL + pgvector + Apache AGE integration: SUCCESS.
- A real PostgreSQL service and extension pair were exercised by CI.

## Not completed

- Authenticated multi-tenant API and PostgreSQL RLS enforcement.
- Delete/retention/legal-erasure policy, orphaned AGE vertex cleanup.
- Device evidence trust, clock drift/fencing, and production observability.
- Benchmarks on actual household memory corpora: Recall@k, P95, resources.
- Local Chinese embedding inference pipeline, model lifecycle.
- Full interval-splitting bitemporal semantics and historical graph traversal.
- Temporal / Restate runtime adapters and broader conformance testing.

## Acceptance scope

**MVP storage goal met**: one PostgreSQL engine performs SQL, vector and
graph retrieval, with independent local object storage.

**Production goal not yet met**: the project must not be claimed secure,
fully compliant, or ready for untrusted multi-tenant deployments.
