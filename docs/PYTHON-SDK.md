# Python SDK

The package includes a dependency-free synchronous client:

```python
from agent_memory_core.client import MemoryClient

memory = MemoryClient(
    base_url="http://127.0.0.1:8765",
    api_key="YOUR_MEMORY_API_KEY",
)
print(memory.capabilities())

created = memory.remember(
    {
        "subject_id": "self",
        "predicate": "language_preference",
        "value": {"preferred": "zh-CN"},
        "source_id": "agent:conversation-001",
        "polarity": "positive",
    },
    idempotency_key="conversation-001-language",
)

facts = memory.recall("self")
hits = memory.search_lexical("卧室保持安静")
page = memory.events(limit=100)
# Persist page["next_cursor"] and pass it as after= on the next poll.
```

The client does not retry writes automatically. Use an idempotency key when
retrying `remember`; the server rejects key reuse with a different body using
HTTP 409, surfaced as `MemoryAPIError.status_code`. The client also exposes
`process_candidates` and `extract_text`; the latter requires a server with a
loopback local model extractor configured.
