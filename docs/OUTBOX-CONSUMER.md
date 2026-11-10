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
- Each poll currently scans and registers all unseen matching events for the
  selected consumer. Large backlogs need bounded registration and monitoring.
- The five-second retry delay is fixed, not exponential or jittered.
- This is **at-least-once**; external HTTP subscribers require a stable
  idempotency key and independent downstream acknowledgements.
- Dead letters must be inspected and requeued by an authorized operator.
- Legacy `memory_outbox.processed_at` and the new delivery ledger coexist;
  never interpret the legacy flag as success for every projection.
