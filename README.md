# Agent-Memory-Core

Local-first, evidence-aware, time-versioned Agent memory infrastructure with pluggable storage and runtime adapters.

> v0.1.0 is an experimental reference implementation, not production-ready software.

The project separates **memory facts and provenance** from workflow execution (Temporal, Restate, or local state machines) and from independent verification of physical device state.

## Target: PostgreSQL triple-store (design accepted, not fully implemented)

- PostgreSQL: canonical facts, event/time history, references and transactional metadata.
- pgvector: optional 384-dimensional semantic index; requires local embedding generation.
- Apache AGE: optional graph projection and Cypher traversal; AGE projection writer is **not implemented**.
- Object bytes remain in local content-addressed storage; time-series records may live in PostgreSQL.

See [ADR-0002](docs/ADR-0002-postgres-triple-store.md) and [PostgreSQL setup](sql/postgresql/README.md). The prototype includes SQL migrations, optional Python backend and contract tests, **not a validated integrated AGE deployment**.

## Current capabilities
- SQLite persistence and bitemporal-style as-of queries
- Source identifiers, correction links, and append-oriented history
- Event identifier deduplication and conflict checks
- SHA-256-addressed local object storage
- Tenant-scoped application queries
- Offline implementation with no remote model API requirement

## Not yet implemented
- Authenticated multi-tenant access control and cryptographic protection
- Complete bitemporal interval revision semantics
- Apache AGE graph projection and production-ready PostgreSQL integration tests
- Embedding, hybrid retrieval, graph queries
- Temporal / Restate integration
- Production-grade deletion, retention, backup, and observability

## Quick start

```bash
python -m pip install -e .
python -m pytest -q
python examples/demo.py
```

See `docs/ADR-0001-boundaries.md` and `docs/ROADMAP.md` for boundaries and roadmap.

## License

Apache-2.0.
