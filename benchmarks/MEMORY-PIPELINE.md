# Memory pipeline benchmark

The benchmark runner measures a bounded slice of the actual PostgreSQL path:

1. Concurrent canonical candidate writes through the atomic candidate API.
2. FTS destination job processing through ProjectionWorker.
3. Tenant-scoped lexical query P50/P95 and mean latency.

Run against a dedicated tenant because benchmark claims and outbox/projection
rows are persistent:

```bash
python scripts/benchmark_memory_pipeline.py \
  --tenant-id benchmark-disposable-2026-11-09 \
  --count 500 --concurrency 16 --search-iterations 200
```

The runner reports write ops/s, FTS jobs/s, queue backlog, and lexical query
latency. It does not benchmark vector embedding quality, AGE traversal, SSE,
external webhooks, 100-agent load, failover or database restart recovery.
Treat results as a reproducible smoke benchmark, not production capacity.
