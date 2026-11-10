# Runbook — combined local PostgreSQL instance

Requires Docker + Compose, port 55432 available.

```bash
docker compose up -d --build --wait
python -m pip install -e '.[dev,postgres]'
export MEMORY_TEST_DSN='postgresql://memory:local-dev-only@127.0.0.1:55432/memory'
python -m pytest -q -m integration
python scripts/check_extensions.py
docker compose down -v
```

`down -v` deletes development database data; do not run against valuable data.

Troubleshooting: the integration image is based on Apache AGE's Docker distribution
and installs pgvector for the image's PostgreSQL major version. If Docker builds
fail because the image version changes, pin both base image and matching
`postgresql-XX-pgvector` package before proceeding. Do not silently skip integration.

## Acceptance

- PostgreSQL loads both extensions in **the same database**.
- Claim insertion, time-aware SQL retrieval and vector query work.
- Canonical edge projection yields AGE one- and two-hop query results.
- Re-running projection produces no duplicate graph edges.
- Other-tenant queries do not return these test entities.

## Limitations

This is not production deployment. The password is a local development example.
Run on loopback and never publish as an internet-accessible service.

## Concurrency and projection verification (draft PR #2)

Run against a **disposable test database** with the extensions/migrations installed:

```bash
export MEMORY_TEST_DSN='postgresql://...'
python scripts/benchmark_memory_concurrency.py --agents 100 --concurrency 16
```

The output reports request throughput, P50/P95 latency, active claim count,
and whether all 100 independent provenance links survived. This represents
100 Agent requests with at most 16 concurrent connections, **not** 100
simultaneous database connections; raise `--concurrency` only when the test
server's PostgreSQL connection limit is configured accordingly.

For the FTS worker (after `db.migrate()`):

```bash
export MEMORY_DSN='postgresql://...'
python -m agent_memory_core.projection_worker --once
python -m agent_memory_core.projection_worker --batch-size 20 --poll-seconds 1
```

Monitor independent consumer status through
`ProjectionOutboxWorker(db.conn).status(consumer_id="fts-v1", tenant_id="home")`.
A failed or lagging projection does not alter canonical memory; diagnose
`pending`, `dead_letter`, and `oldest_pending_seconds` before requeueing.

**Do not** reuse these development credentials or benchmark tenants in a
production system. Backup/restore, endpoint RLS, worker supervision and
sustained 100-connection load have not been established by this script.
