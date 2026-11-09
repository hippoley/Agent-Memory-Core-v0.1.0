# Transactional outbox: delivery and recovery contract

The PostgreSQL outbox is an **at-least-once** delivery primitive. The worker uses `FOR UPDATE SKIP LOCKED` so multiple workers can claim different rows. For database-local handlers, the handler's SQL effects and the outbox acknowledgement commit in one transaction. If the handler raises, its writes roll back and the worker records the failure in a separate transaction.

## Worker API

```python
from agent_memory_core.outbox import OutboxWorker, persist_verification_receipt

worker = OutboxWorker(conn, max_attempts=8, base_retry_seconds=2, max_retry_seconds=300)
processed = worker.consume(event_type="memory.claim.verified", handler=persist_verification_receipt, limit=50)
print(worker.stats())
```

- Attempts use bounded exponential backoff: 2, 4, 8, ... seconds, capped at `max_retry_seconds`.
- An event reaching `max_attempts` receives `dead_lettered_at` and is excluded from normal polling. It does not block later events of the same type.
- `last_attempt_at`, `attempts`, and `last_error` provide operator evidence.
- `retry_dead_letter(event_id=...)` explicitly requeues a dead letter. By default it resets the attempt counter; set `reset_attempts=False` to retain the accumulated count.
- `stats(event_type=...)` reports pending, processed, dead-lettered and total attempts. It is a snapshot, not a monitoring/alerting integration.

## Handler contract and delivery semantics

A database-local handler receives `(conn, tenant_id, event_id, aggregate_id, payload)`. It must use the supplied connection and must not commit, open an independent transaction, or perform non-idempotent external side effects. The stable event ID is the idempotency key for downstream writes.

For external destinations (webhooks, brokers, remote vector services), a database transaction cannot atomically commit the remote side effect and the local acknowledgement. Use a destination-side idempotency key and a reconciliation strategy; do not claim exactly-once delivery.

## Current implementation boundary

The verified adapter persists Temporal memory-verification receipts into `memory_events`. The worker now has bounded retry, failure timestamps, dead-letter isolation and explicit operator requeue.

Still not implemented: independent per-destination delivery rows for a single canonical event; embedding generation; FTS projection; AGE projection acknowledgement; webhook subscription delivery; a supervised daemon and production alerting. A single `processed_at` still means only that this particular outbox event was handled, not that every derived index is current.


## Per-destination projection fan-out

Inserting a `memory.claim.created` outbox event atomically creates independent
`fts`, `vector`, and `age` rows in `memory_projection_jobs`. This prevents
one destination's failure or acknowledgement from hiding another destination's
state. The trigger is idempotent on `(event_id, projection_name)`.

```python
from agent_memory_core.projection_jobs import ProjectionWorker

worker = ProjectionWorker(conn, max_attempts=8)
worker.consume(projection_name="fts", handler=fts_handler, limit=100)
print(worker.stats())
```

Each destination independently transitions through pending/retry, succeeded,
or dead-letter. A failed vector projection does not block FTS or AGE. Operators
can requeue one destination with
`retry_dead_letter(event_id=..., projection_name="vector")`.

The fan-out and queue lifecycle are implemented; the actual FTS, embedding
generation/vector upsert, AGE projection handlers, webhook delivery and
supervised worker process are still separate implementation tasks. Do not
interpret a queued destination as an implemented projection.


Projection workers accept an optional tenant_id on consume and stats. Use it
when operating a tenant-scoped worker or running isolated tests; omitting it
runs a shared worker across all tenants. Tenant identity is always carried in
the claimed job and must be passed through to every destination handler.


## First concrete projection adapter: FTS + CJK substring lookup

`agent_memory_core.fts.project_claim_to_fts` materializes the canonical claim
into `memory_fts_documents`. It is idempotent on (tenant_id, memory_id) and
uses PostgreSQL `tsvector` for lexical ranking plus `pg_trgm`-backed substring
matching for Chinese text. This avoids pretending that PostgreSQL's built-in
simple parser performs Chinese word segmentation.

Run it through the destination worker:

```python
from agent_memory_core.projection_jobs import ProjectionWorker
from agent_memory_core.fts import project_claim_to_fts

ProjectionWorker(conn).consume(
    projection_name="fts",
    tenant_id="home",
    handler=project_claim_to_fts,
    limit=100,
)
```

Query with `search_fts(conn, tenant_id="home", query="卧室保持安静")`. The
tenant predicate is mandatory. This is lexical/substring retrieval, not
semantic vector search; vector and AGE handlers remain separate.


## Second concrete projection adapter: Apache AGE claim nodes

`AgeProjection.project_claim_event` projects a canonical claim into a fixed
`MemoryClaim` node label. It uses a tenant-qualified key, escaped scalar
properties, and a PostgreSQL projection ledger. The graph node and ledger row
are committed with the per-destination projection-job acknowledgement.

Initialize AGE once per worker connection, then consume:

```python
from agent_memory_core.age_graph import AgeProjection
from agent_memory_core.projection_jobs import ProjectionWorker

age = AgeProjection(conn)
age.initialize()
ProjectionWorker(conn).consume(
    projection_name="age",
    tenant_id="home",
    handler=age.project_claim_event,
    limit=100,
)
```

The AGE claim-node adapter and its real PostgreSQL+AGE integration test are
implemented. Existing edge projection remains a separate path. Graph nodes are
rebuildable indexes; canonical claim facts remain authoritative in PostgreSQL.


## Third concrete projection adapter: injected local vector embedding

`VectorProjection` accepts an application-provided embedder and stable
`model_id`. It reads the canonical claim, constructs a text representation
including assertion polarity, validates the returned 384D/512D vector, checks
the tenant/model dimension registry, and upserts into pgvector. It never
downloads or invokes a model itself.

```python
from agent_memory_core.vector_projection import VectorProjection
from agent_memory_core.projection_jobs import ProjectionWorker

# local_embed(text) must call your locally hosted embedding model.
adapter = VectorProjection(embedder=local_embed, model_id="bge-small-zh-v1.5")
ProjectionWorker(conn).consume(
    projection_name="vector", tenant_id="home", handler=adapter, limit=100
)
```

The integration test uses a deterministic synthetic vector to verify
transactional wiring and pgvector retrieval, not embedding quality. A real
local model must still be benchmarked for Chinese semantic recall, latency and
memory use before production selection.


## Supervised worker process

Install the project and run one independent process per destination:

```bash
# FTS (no model dependency)
agent-memory-projection-worker --projection fts --tenant-id home

# AGE (initializes AGE/graph on worker connection)
agent-memory-projection-worker --projection age --tenant-id home

# Vector (inject the application's local embedding callable)
export MEMORY_EMBEDDER='my_home_models.embed:embed_text'
export MEMORY_EMBEDDING_MODEL_ID='bge-small-zh-v1.5'
agent-memory-projection-worker --projection vector --tenant-id home
```

Set `MEMORY_DSN`; `MEMORY_TENANT_ID` is optional (omission runs a shared
worker across tenants). Use `--once` for a single batch, `--batch-size` for
bounded work, and `--poll-interval` to tune idle polling. SIGTERM/SIGINT
trigger graceful shutdown. Database errors cause reconnect attempts; handler
errors are recorded by the projection queue's retry/DLQ policy.

Run these as separate services/processes so a slow or unavailable destination
cannot block another projection type. This is a supervised polling worker,
not yet a Kubernetes operator, autoscaler or production metrics/alerting stack.
