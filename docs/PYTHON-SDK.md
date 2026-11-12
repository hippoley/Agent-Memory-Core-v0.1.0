# Python SDK

The supported client is `agent_memory_core.sdk.MemoryClient`. It uses the
public HTTP API and never connects directly to PostgreSQL.

Install with `pip install -e '.[api]'`.

```python
from agent_memory_core.sdk import MemoryClient

with MemoryClient("http://127.0.0.1:8765", "YOUR_MEMORY_API_KEY") as memory:
    print(memory.ready())
    print(memory.capabilities())
    created = memory.remember(
        subject_id="bedroom",
        predicate="noise_preference",
        value={"level": "quiet"},
        source_id="agent:conversation-001",
        valid_from="2026-11-12T00:00:00Z",
        idempotency_key="conversation-001-bedroom-noise",
    )
    facts = memory.recall("bedroom", predicate="noise_preference")
    hits = memory.search(query="卧室晚上安静一点", limit=10)
```

Tenant identity is derived by the server from the API credential. Do not send
a tenant ID. Use an idempotency key when retrying a write after a timeout: the
same key and body return the original result; reusing the key with a different
body returns HTTP 409.

The SDK exposes `events(after=..., limit=...)` and `stream_events(after=...)`
for cursor polling and SSE. Persist the cursor and reconnect after restarts.

Webhook methods: `create_webhook`, `list_webhooks`, `revoke_webhook`,
`webhook_deliveries`, and `replay_webhook_delivery`. Delivery is at-least-once;
receivers must verify HMAC over the raw body and deduplicate by delivery ID.

`MemoryAPIError` exposes `status_code`, `code`, and `detail`. Transport
failures use status code 0 because no HTTP response was received. Never
blindly retry writes without an idempotency key.
