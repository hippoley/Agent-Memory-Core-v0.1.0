# US-C02 · Reconcile UNKNOWN after revocation, never retry execution

A strict creator-authorized task may have already sent a remote command when
its authorization is revoked. The resulting local Effect may be UNKNOWN
because the remote ACK was lost. If revocation also prevents every recovery
lookup, the system can neither safely retry nor determine what happened.

`WorkerContext.assert_lease(reconciliation_only=True)` checks the *current*
tenant-scoped Worker lease/fencing epoch without granting the revoked creator
any write authority. Only the **trusted Provider.reconcile()** lookup of an
existing UNKNOWN effect uses it. The normal `ctx.execute_effect()` path still
requires current creator writable membership (owner/admin/editor).

The existing Effect Ledger restricts reconciliation to UNKNOWN, and refuses
automatic re-execution unless authoritative lookup reports NOT_FOUND. When the
creator is revoked, NOT_FOUND can return the effect to requested but CANNOT
authorize the new execute: the WorkerContext write gate remains active.
The strict task's database terminal-completion gate remains active too.

Real PostgreSQL regression:
`tests/test_us_c02_revoked_readonly_reconcile.py` — one remote execute
raises synthetic lost ACK, then creator is downgraded to reader; a second
requested effect cannot be executed, but the UNKNOWN effect can be resolved
by read-only provider lookup with the same idempotency key. Attempt count
remains 1 and task cannot be marked completed after revoke.

**Security caveat:** This exemption is safe only when Provider.reconcile is
a genuine **read-only** lookup with independently restricted credentials.
AMC's Python Protocol cannot prevent a malicious provider adapter from
mutating devices inside `reconcile`. Production adapters and physical
gateways must separate read-only reconciliation credentials from actuation
authority. This change does not confer delegated Agent B execution rights,
physical-causality evidence or external adoption.
