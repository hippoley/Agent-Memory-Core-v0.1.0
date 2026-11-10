# Transactional outbox: supported and unsupported semantics

There are now two consumers. The existing `OutboxWorker` preserves the
single-event processed flag behavior for existing Temporal receipts.

`ProjectionOutboxWorker` provides a separate durable delivery receipt for
each (`event_id`, `consumer_id`) pair. One consumer's success never marks
another consumer's event as delivered. Registration reads canonical
`memory_outbox` entries, including entries previously handled by a legacy
consumer. PostgreSQL-local handlers execute with the receipt update in the
same transaction using `FOR UPDATE SKIP LOCKED`. On failure the handler
transaction rolls back, a separate transaction increments retries, and after
`max_attempts` the delivery enters a dead-letter state.

## Example

```python
from agent_memory_core.outbox import ProjectionOutboxWorker

worker = ProjectionOutboxWorker(db.conn)
worker.consume(
    consumer_id="fts-v1",
    event_type="memory.claim.created",
    handler=project_fts_using_same_connection,
    limit=20,
    max_attempts=5,
)
```

The handler accepts `(conn, tenant_id, event_id, aggregate_id, payload)`.
It must **not commit** its own PostgreSQL transaction. Only a trusted
operator should invoke:

```python
worker.retry_dead_letter(
    tenant_id="home", consumer_id="fts-v1", event_id="event-uuid")
```

Consumers can be named `vector-v1`, `fts-v1`, `age-v1`, or a custom
subscriber. The ledger records those names but does **not** create their
actual projection handlers. A successful no-op handler proves receipt
mechanics only, not functioning vector or AGE projection.

## Known limitations

- No provided vector embedding worker or canonical FTS materializer yet.
- No automatic dispatch daemon or background worker lifecycle.
- Each poll registers a bounded ordered batch of unseen events for the selected
  consumer (up to 1,000); high-volume backlogs still need metrics and tuning.
- Retry uses capped exponential delays (5 to 300 seconds) without jitter.
- This is **at-least-once**; external HTTP subscribers require a stable
  idempotency key and independent downstream acknowledgements.
- Dead letters must be inspected and requeued by an authorized operator.
- Legacy `memory_outbox.processed_at` and the new delivery ledger coexist;
  never interpret the legacy flag as success for every projection.

## Real lexical projection worker (FTS-v1)

The repository now includes a built-in PostgreSQL-local FTS handler:

```bash
# First apply the SQL migration through db.migrate() as an operator.
export MEMORY_DSN='postgresql://...'
python -m agent_memory_core.projection_worker --consumer-id fts-v1 --batch-size 20
# For a single bounded poll:
python -m agent_memory_core.projection_worker --once
```

`project_claim_fts` reads the canonical tenant-scoped JSON value and updates
`memory_claim_fts` in the same transaction as the consumer receipt.
`PostgresMemory.fts_search(...)` enforces tenant and bitemporal scope.
`HybridMemorySearch` can add the `fts` rank to its reciprocal-rank
fusion alongside the existing SQL/pgvector/AGE paths.

This materializer is **not** a Chinese NLP tokenizer: the stock PostgreSQL
`simple` configuration is a token-based baseline. It must be evaluated
against a labeled Chinese benchmark and replaced/extended with an appropriate
Chinese tokenizer before claiming Chinese full-text search quality.
Index lag is possible while the worker is down; canonical SQL remains the
source of truth. A production supervisor, metrics, stale-projection alarms,
resync controls and recovery exercises are still required.

## Apache AGE projection worker (draft)

After running schema migration, start a separately supervised AGE worker:

```bash
export MEMORY_DSN='postgresql://...'
python -m agent_memory_core.projection_worker --projection age --once
python -m agent_memory_core.projection_worker --projection age --consumer-id age-v1
```

The AGE worker independently acknowledges `memory.edge.created`,
`memory.edge.retired`, and `memory.claim.created` events using the
`age-v1` consumer namespace. Canonical edge creation/retirement now append
transactional outbox events. New claims trigger reconciliation for previously
sourced edges whose claim has been superseded. The handler uses
`AgeProjection.reconcile_invalidated` and `project_pending` in the
same PostgreSQL transaction as its receipt.

**Boundaries:** AGE remains a rebuildable index and is eventually consistent
when this worker is stopped. Reconciliation is bounded to 100 edges per
trigger and is not a guaranteed full rebuild for arbitrary historic backlog.
Existing edge rows created before the event-producing version require a
separate backfill or explicit `AgeProjection.project_pending` sweep.
The graph's tenancy is enforced in canonical SQL; direct AGE access is still
an operator privilege and is not isolated by database RLS.

### Historical AGE repair

Edges written before `memory.edge.created` events were added cannot be
discovered retroactively by the event consumer. Run an explicitly scoped
operator repair without mutating canonical edges:

```bash
python -m agent_memory_core.projection_worker \
  --backfill-age-tenant 'home' --batch-size 100
```

The command reports `projected`, `removed`, and `complete`. A non-complete
result exits nonzero so the operator can run another bounded pass. It does
not remove graph orphan vertices; it replays unprojected active edges and
cleans retired/superseded ones from the derived graph.

## Offline pgvector projection (draft)

This worker uses the same per-consumer ledger as AGE and FTS. Its inference
adapter loads an already-downloaded SentenceTransformer from an **existing
local directory**, with `local_files_only=True` and
`trust_remote_code=False`. No hosted embedding API is called.

```bash
python -m pip install -e '.[postgres,embedding]'
export MEMORY_DSN='postgresql://...'
python -m agent_memory_core.projection_worker \
  --projection vector \
  --tenant-id home \
  --model-id local-bge-zh-512-v1 \
  --model-path /srv/models/embedding-zh \
  --batch-size 20
```

The loader must yield **384 or 512** dimensions, finite and nonzero.
Model IDs are versioned operator-assigned identifiers: never reuse the same
model ID for incompatible model weights or normalization rules. Per-tenant
model-dimension conflicts fail without an outbox acknowledgement. Retries
and dead-letter replay are available through `ProjectionOutboxWorker`.

`model_path` needs to be populated by the operator ahead of time; the CI
embedding test uses a deterministic fixture and **does not demonstrate
inference with real model weights**. Model quality, Chinese similarity and
hardware throughput require separate tests with actual local weights.

For now, inference runs inside the database transaction so vector storage
and its receipt commit atomically. For production, isolate potentially slow
model inference from PostgreSQL row locks with a leased prepare/commit
protocol; external inference has at-least-once semantics, not exactly once.
The vector consumer requires an explicit `--tenant-id`, preventing accidental
processing of unrelated tenants. FTS and AGE workers may also accept the
optional `--tenant-id` filter.
