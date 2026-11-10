# Durable projection delivery

Canonical writes create projection delivery rows in the same PostgreSQL transaction as the claim, canonical event and outbox row. Current claim channels are `fts` and `vector`. Each `(event_id, channel)` has an independent state machine: `pending -> done` or `pending -> dead`. Schema is kept in packaged and standalone SQL migrations.

## Runtime

Run with `MEMORY_DSN` configured:

```bash
python -m agent_memory_core.projection_worker
```

FTS is built in and writes a tenant-scoped `tsvector` projection with a GIN index. Vector generation is injected for a local embedding model:

```bash
MEMORY_EMBEDDER=my_local_embeddings:embed python -m agent_memory_core.projection_worker
```

The callable receives a canonical claim dictionary and returns `(model_id, vector)`. Dimensions 384 and 512 are supported; PostgreSQL enforces model ID to dimension binding. Without an embedder, vector tasks remain pending rather than being falsely acknowledged.

## Delivery semantics

`ProjectionWorker` uses `FOR UPDATE SKIP LOCKED` and runs each database-local handler and acknowledgement in one transaction. On failure, handler writes roll back; retry state is updated separately with bounded exponential backoff. After the attempt budget the task becomes `dead`. Operators can call `requeue_dead(event_id=..., channel=...)` to replay it.

Delivery is **at least once**, not exactly once. Handlers must use the supplied connection, must not commit, and should be idempotent. Never perform remote HTTP calls inside the handler transaction.

## Remaining boundaries

- AGE graph edges still use `AgeProjection`'s explicit SQL/AGE ledger path; event-to-edge mapping and a worker adapter are not wired.
- External webhook subscriptions, signing, per-subscriber cursors and delivery reconciliation are not implemented.
- FTS is projected, but public hybrid search has not yet switched fully to ranked FTS retrieval.
- Process supervision, metrics, health endpoints and graceful deployment strategy remain to be added.