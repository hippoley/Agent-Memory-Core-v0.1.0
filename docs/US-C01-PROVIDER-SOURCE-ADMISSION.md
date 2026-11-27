# US-C01 · Provider-side live source admission (controlled benchmark)

After the source verification / DB-intent atomicity fix, there was still a
window between committing the fenced effect intent and its remote Provider
commit. The Provider only checked the worker lease, epoch and request hash.

A source-verified `register_intent` now persists the accepted immutable
`source_id`, scoped subject and predicate in the task checkpoint **within
the same transaction** as the effect intent. The controlled
`provider_fence` then queries current source registry, revision and issuer
key status at the actual HTTP POST admission, holding share locks over the
test Provider's SQLite commit. An updated or revoked source is denied even
when worker ID, epoch, bearer token and effect hash remain valid.

This is NOT a production across-database transaction or a general RLS
proof. This reference Provider is test-only, uses an admin-style DB login,
and cannot enforce independent action-scoped Agent B delegation. The extra
check is limited to checkpoints carrying `source_authority`; diagnostic
generic effects without a source gate remain source-unbound. Never
advertise that path as source-authorized execution.

A control must show v3 active succeeds; the negative path must rotate v3
to v4 AFTER intent registration but BEFORE Provider POST, assert HTTP 403
and zero SQLite effects, then re-activate v3 and admit once. If executed
before these checks, results are unverified. Read-only reconciliation must
continue to use existing fenced recovery semantics.
