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
