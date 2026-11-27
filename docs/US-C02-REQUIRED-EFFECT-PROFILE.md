# US-C02 · Strict required-effect task profile

Task type `continuity.effect.v1` opts into an immutable **required effect manifest**:
```json
{"task_type":"continuity.effect.v1","input":{"goal":"synthetic operation","required_effect_keys":["set-bedroom-target-v1"]}}
```
Creation rejects empty, repeated, blank, too-long, or excessive (more than 20)
effect keys. Worker code must checkpoint the intent using the same effect key
and execute/reconcile via `DurableTaskEffectRuntime`. Worker completion uses
the *persisted* task manifest, checking all required keys are present and
`committed`; omitted/unknown/requested/failed required effects block
`completed` with a lease conflict. Generic task types retain prior semantics.

This protects an **opted-in task type** from the common failure of a handler
returning success without ever registering an essential effect. It is not
proof of independent physical evidence: `committed` is a ledger/provider
acknowledgement, and a client that deliberately creates another task type has
not opted into this policy. Do not treat this task profile as an authorization
mechanism or a universal requirement for arbitrary task types.

Real PostgreSQL + Worker + REST + fake provider vertical check:
`tests/test_us_c02_worker_effect_terminal_vertical.py`.

External production gateways still need stable remote idempotency,
authoritative reconciliation and independent physical observations.
