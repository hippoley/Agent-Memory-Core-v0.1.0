# Transactional outbox: supported and unsupported semantics

`OutboxWorker.consume(event_type=...,handler=...,limit=...)` uses
`FOR UPDATE SKIP LOCKED` to claim one pending row and invokes a database-local
handler in the same PostgreSQL transaction. The event acknowledgement and the
handler's SQL effects commit together; if the handler fails, its changes roll
back and the event is rescheduled with a recorded error.

Verified adapter: `memory.claim.verified` receipt from Temporal is consumed
into canonical `memory_events` using event ID idempotency.

Not yet implemented:
- an embedding worker that produces vectors from raw text,
- full SQL FTS projection and AGE task acknowledgement,
- per-channel task accounting for a single `memory.claim.created` event,
- background daemon service lifecycle and retry exhaustion/DLQ,
- external side effects exactly-once semantics.

A durable outbox is an at-least-once delivery primitive. Do not use one
`processed_at` flag to claim that all of embeddings, AGE, FTS and remote
side effects have completed; split them into independent durable tasks.
