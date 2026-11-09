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
