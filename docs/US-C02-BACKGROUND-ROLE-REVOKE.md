# Background effect authorization must not outlive role downgrades

AMC's HTTP layer already rejects write requests from a tenant `reader`.
Previously `IdentityStore.authorize_background_execution()` checked that the
principal, tenant, membership and bound session/API credential were active,
but failed to check the **current membership role** was writable. A queued
activity that omitted `membership_version` could therefore continue to pass
the permission check after its principal was downgraded to reader.

The canonical authorization check now requires owner/admin/editor membership,
regardless of whether the caller passes a membership version. This affects
both session- and credential-backed queued work. It is fail-closed for reader
roles but does **not** itself grant Agent B any device or `tasks:execute`
authority; that remains a separate scoped capability.

Real PostgreSQL regression is extended in
`tests/test_identity_store.py::test_queued_execution_is_denied_after_session_or_membership_revocation`
to test downgrade with and without version enforcement and with both session
and credential references. This is an authorization consistency repair,
not a physical-device test.
