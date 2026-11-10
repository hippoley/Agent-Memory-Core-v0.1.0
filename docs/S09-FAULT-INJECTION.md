# S09 Worker crash and retry-leasing acceptance

The durable Webhook delivery is **at least once**. The delivery ID and event ID
must be deduplicated by receivers. The worker is allowed to crash at any point
without losing the delivery task.

## Implementation

The delivery claim is committed in PostgreSQL before outbound network I/O.
Every claim increases `attempts` and stores `lease_until`. Expired claims are
eligible for reclamation by another worker. Acknowledgement or failure update
is fenced by both `attempts` and the unexpired lease:

```sql
UPDATE memory_webhook_deliveries
SET status='delivered',delivered_at=clock_timestamp()
WHERE delivery_id=$1 AND status='delivering'
  AND attempts=$2 AND lease_until>clock_timestamp();
```

A slow or crashed worker cannot overwrite an attempt subsequently claimed by
a replacement worker. A response arriving after its lease has expired is
deliberately not accepted. The replacement may send the same event again;
this is why receiver-side idempotency is required.

## Real integration evidence

`tests/test_s09_worker_crash.py` verifies in real PostgreSQL:

- A separate Python process acquires and commits a lease and is killed with
  SIGKILL, without a graceful shutdown or acknowledgement.
- The lease remains durable and is not available for premature reuse.
- After an explicit database lease-expiration boundary, another worker
  claims the same delivery with incremented attempt number.
- An old attempt cannot mark the reclaimed delivery complete.
- The current attempt can acknowledge the event exactly once.
- A 64-event tenant backlog drains into 64 unique delivery IDs with no
  pending deliveries and no silent data loss.

## Remaining S09 gates

This does not yet verify an actual PostgreSQL service restart, storage failure
during WAL flush, backup/restore point-in-time recovery, or a long-running
100-agent concurrent backlog. The receiver in the backlog test uses HTTPX's
test transport; external network/TLS receiver acceptance is tracked under S08.

The normal CI integration workflow exercises crash recovery; disruptive
database restarts and backup/restore tests belong in an isolated, manually
scheduled environment, not the shared test database.
