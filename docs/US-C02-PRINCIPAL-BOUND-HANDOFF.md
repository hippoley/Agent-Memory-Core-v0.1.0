# US-C02 · Principal-bound Handoff v1 (opt-in)

The original `source_agent` and `target_agent` are descriptive labels.
They **do not authenticate or authorize** any party, even when the session
is otherwise authenticated. A legacy handoff is returned with
`principal_bound: false` at acceptance.

A new Handoff POST may include `target_principal_id`, a UUID of an existing
active tenant Principal. This **opts into the strict** principal-bound mode.

1. The API derives the **source** Principal ID exclusively from a validated
   session or API credential; a legacy shared bearer key cannot create a bound
   handoff. The caller must also be the recorded creator of this task.
2. The target must be a distinct, active Principal with active owner/admin/editor
   membership in the same tenant.
3. The immutable, hash-checked Handoff payload contains
   `delegation.schema=amc.principal-handoff.v1`, source/target Principal IDs,
   target membership version and `scope=context_accept_only`.
4. At **every** acceptance, including repeated acceptance, the server checks
   that the authenticated current Principal is the exact target and that
   the target membership is still active, writable, and at the frozen version.
   Revoked/expired credentials fail at the existing API authentication layer.
5. A 403 is used for target Principal mismatch, membership change, and
   unauthorized creation. Legacy Handoff acceptance remains compatible and
   explicitly reports `principal_bound:false`.

Example (A and B have independently issued credentials):
```python
offer = agent_a.create_handoff(
    task_id=task_id, source_agent="agent-A", target_agent="agent-B",
    subject_ids=["room-1"], target_principal_id=target_principal_uuid)
accepted = agent_b.accept_handoff(offer["handoff_id"], target_agent="agent-B")
assert accepted["principal_bound"] is True
```

**Important limits:** This is scoped to **context acceptance**. It does NOT
delegate worker `tasks:execute`, physical device control, or a blanket grant
to write memories. The active tenant's ordinary access control still applies.
The task creator may only delegate a task it created; shared-key legacy tasks
without a creator cannot be upgraded by sending a UUID. Historical, unbound
handoffs retain their old descriptive semantics and should never be interpreted
as authenticated delegation evidence. Existing JWT/OIDC provider and credential
issuance/revocation mechanisms are reused; no new identity store is created.
