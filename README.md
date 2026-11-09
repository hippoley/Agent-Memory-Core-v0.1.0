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
- Restate and WindowPilot adapters; Temporal has an optional SDK workflow/worker prototype with real PostgreSQL Activities, but not a production deployment
- Production-grade deletion, retention, backup, and observability

## Public interface prototype\n\nAn optional local REST gateway exposes `/v1/health`, `/v1/capabilities`, `/v1/memories`, and `/v1/search`. Install `.[api,postgres]`, set `MEMORY_DSN`, `MEMORY_API_KEY` (24+ characters), and `MEMORY_TENANT_ID`, then run `uvicorn agent_memory_core.server:app --host 127.0.0.1 --port 8765`. The gateway is a single-tenant local prototype; it is not safe for public multi-tenant deployment. The search endpoint accepts caller-generated embeddings and reports degraded optional channels.\n\n## Quick start

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


## End-to-end example

The runnable [end_to_end.py](examples/end_to_end.py) writes real sample entities,
source provenance, a claim, graph edges, a time-series observation, an outbox
event and a content-addressed object into the local stack, then queries AGE.
Temporal has an optional SDK workflow/worker adapter. GitHub Actions starts a real local Temporal development service and verifies PostgreSQL Activities plus idempotent receipts. This is an integration prototype, not a production Temporal cluster or complete execution API.


## Swagger/OpenAPI for third-party integration

- **[中文接入教程与 API 状态说明](docs/SWAGGER_GUIDE.zh-CN.md)**
- Live interactive UI: `http://127.0.0.1:8765/docs` after local launch.
- Machine-readable OpenAPI: `http://127.0.0.1:8765/openapi.json`.
- Regenerate offline: `python scripts/export_openapi.py docs/openapi.generated.json`.
- GitHub Actions `public-api-contract` runs generated schema/security tests and uploads the JSON file as an artifact.

The contract is intentionally limited to implemented routes; graph, objects, time-series writes and Temporal workflows currently require Python modules, not HTTP. See the Chinese guide for complete examples and exclusions.
