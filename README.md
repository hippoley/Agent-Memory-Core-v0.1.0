# Agent-Memory-Core

Local-first, evidence-aware, time-versioned Agent memory infrastructure with pluggable storage and runtime adapters.

> v0.1.0 is an experimental reference implementation, not production-ready software.

The project separates **memory facts and provenance** from workflow execution (Temporal, Restate, or local state machines) and from independent verification of physical device state.

## PostgreSQL triple-store (tested prototype)

- PostgreSQL: canonical facts, event/time history, references and transactional metadata.
- pgvector: separate 384D/512D vector indexes with per-tenant model dimension registry and exact bitemporal retrieval; requires local embedding generation.
- Apache AGE: graph projection from canonical SQL edges, restart-safe ledger, bounded Cypher traversal and retraction reconciliation, tested with PostgreSQL in CI.
- Object bytes remain in local content-addressed storage; time-series records may live in PostgreSQL.

See [ADR-0002](docs/ADR-0002-postgres-triple-store.md) and [PostgreSQL setup](sql/postgresql/README.md). CI builds a single PostgreSQL instance with pgvector and AGE and verifies SQL, vector, graph, idempotent projection, and tenant query isolation. This remains a **prototype**, not a secured production deployment.

## Current capabilities
- Same-database PostgreSQL + pgvector + AGE integration test, verified in GitHub Actions
- SQL / vector / graph hybrid retrieval with canonical time/tenant filtering
- AGE restart-safe idempotent projection of immutable canonical edges
- SQLite persistence and bitemporal-style as-of queries
- Source identifiers, correction links, and append-oriented history
- Event identifier deduplication and conflict checks
- SHA-256-addressed local object storage
- Tenant-scoped application queries
- Offline implementation with no remote model API requirement

## Not yet implemented
- Authenticated multi-tenant access control and cryptographic protection
- Complete bitemporal interval revision semantics
- Automatic local embedding model, embedding model/version lifecycle and relevance evaluation
- Full historical graph snapshots, orphan-vertex garbage collection and general graph rebuild/migration
- Temporal / Restate integration
- Production-grade deletion, retention, backup, and observability

## Quick start

```bash
python -m pip install -e '.[dev,postgres]'
python -m pytest -q
python examples/demo.py
```

See `docs/ADR-0001-boundaries.md` and `docs/ROADMAP.md` for boundaries and roadmap.

## License

Apache-2.0.

## Verified integration

[GitHub Actions combined PostgreSQL/pgvector/AGE test](.github/workflows/integration.yml) builds a real database and runs the `tests/test_pg_runtime.py` suite. Run locally as documented in [RUNBOOK](docs/RUNBOOK.md).

```bash
docker compose up -d --build --wait
python -m pip install -e '.[dev,postgres]'
MEMORY_TEST_DSN='postgresql://memory:local-dev-only@127.0.0.1:55432/memory' pytest -q -m integration
```

All user input is scoped at query time with tenant IDs but **database RLS, authentication, encrypted storage, deletion and production observability remain unimplemented**. Never expose this prototype as a multi-tenant public service.

## Verified milestone (2026-11-09)

The core SQL + vector + AGE retrieval prototype is implemented and has a green
combined PostgreSQL integration workflow. It includes:
- Temporal as-of fact/vector retrieval with a single correction timestamp.
- Repeat-safe AGE edge projection plus invalidation after correction/retirement.
- 384D compatibility and 512D Chinese embedding profile, with legacy backfill.
- Wheel-packaged PostgreSQL schema and schema-parity verification.

This is **not** production-ready multi-tenant memory storage. Runtime
authorization/RLS, retention/erasure, vector relevance benchmarking, automatic
local model inference, complete bitemporal interval revision, and historical AGE
graph queries remain future gates. Do not expose the container credentials or
the service to untrusted networks.
