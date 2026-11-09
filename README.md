# Agent-Memory-Core

Local-first, evidence-aware, time-versioned Agent memory infrastructure with pluggable storage and runtime adapters.

> v0.1.0 is an experimental reference implementation, not production-ready software.

The project separates **memory facts and provenance** from workflow execution (Temporal, Restate, or local state machines) and from independent verification of physical device state.

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
- PostgreSQL / pgvector / Apache AGE or time-series adapters
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
