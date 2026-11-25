# US-C02 · Read-only redacted task effect audit

An authenticated tenant reader can inspect the effects recorded for a durable
task using `GET /v1/tasks/{task_id}/effects` or
`MemoryClient.get_task_effects(task_id)`. Missing task and cross-tenant task
both yield 404; malformed task UUID yields 422. No provider execution is
triggered by this route.

Each effect exposes ONLY: `effect_key`, `request_sha256`, `status`,
`attempt_count`, `provider_reference_recorded`, creation/update/resolution
timestamps and explicit `physical_independence_verified=false` and
`causal_effect_verified=false`. It excludes raw `request`, provider response,
error payload, worker identity and provider reference text; even a committed
effect **does not** prove an independently observed physical result.

Example consumer:
```python
with MemoryClient(url, reader_token, tenant_id=tenant) as auditor:
    task = auditor.get_task(task_id)
    effects = auditor.get_task_effects(task_id)["items"]
    if task["status"] == "completed" and all(
        item["status"] == "committed" for item in effects
    ):
        print("Durable ledger consistent; physical provenance still unverified")
```

This is an **observation API**, not a signature/attestation protocol. It does
not independently authenticate an Agent or establish whether a device actually
changed state. Never interpret an empty effect list as proof that a task
performed zero external actions: a task type without effect intent is a
separate policy failure. Follow-up US-C02 milestones must prove authorization,
strict effect-to-task linkage, an independently controlled observer, and a
third-party clean-room continuation.
