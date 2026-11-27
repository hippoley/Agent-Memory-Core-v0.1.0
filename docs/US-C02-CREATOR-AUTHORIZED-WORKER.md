# US-C02 · Opt-in strict creator-authorized execution

Task type `continuity.authorized-effect.v1` extends the existing required
effect manifest semantics of `continuity.effect.v1`. It additionally requires
a task to be created under an authenticated Principal (legacy shared-key tasks
without `created_by_principal_id` are rejected with 422).

The DB-backed `DurableTaskWorker` checks the **current** tenant, Principal
and writable membership (owner/admin/editor) before invoking a handler; the
same check is enforced by `WorkerContext.assert_lease()` before each normal
`ctx.execute_effect()` provider operation, and before reporting task
completion. Revocation/downgrade after task creation therefore rejects queued
execution with a LeaseConflict rather than silently continuing.

This is a **task creator** authorization rule; it is **NOT** delegated
Agent-B permission. A Handoff `context_accept_only` does not authorize
effects. Untrusted handlers can directly bypass `WorkerContext` and call an
external provider; production deployments must run audited handlers and
apply separate gateway-side authentication/authorization, least privilege,
credential rotation and stable provider idempotency. TOCTOU remains possible
between a successful DB check and a remote physical call. Only the remote
provider can ultimately enforce authorization at effect commit time.

No old task type is silently upgraded. Use the new profile explicitly,
and keep US-C02 #68 open until real Provider and independent observer evidence
exist. A single test run is not evidence of actual external adoption.
