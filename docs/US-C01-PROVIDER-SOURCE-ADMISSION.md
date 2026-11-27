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


## Critical follow-up: bind authorization to the original Effect checkpoint

The first gateway patch read the newest task checkpoint. This was unsafe:
an unrelated progress checkpoint, written *after* the authorized Effect
intent, could omit `source_authority` and cause Provider admission to
skip the source check altogether. A valid Worker lease and matching
Effect digest did not prevent that bypass.

The Provider now reads the original `memory_task_checkpoints` row
**identified by the request's task ID, Effect Key, and SHA-256 request
digest**. Missing or malformed checkpoint rows, or an embedded
`effect_request` mismatch, are rejected. Later progress checkpoints
cannot replace the Effect's source restrictions.

The existing PostgreSQL + HTTP + SQLite cross-process integration test
writes an unrelated checkpoint *after* creating the source-gated intent,
then revokes v3 to v4, and demands that a valid signed-in Worker POST
still return 403 with **zero external effects**. The positive v3 path
must still execute. This is a regression for an actual detected code
bypass, not a blanket physical-world authorization claim.


## Cross-process Agent B must read the same effect-bound intent

An adversarial later progress checkpoint also exposed a second bug: the
isolated Agent B was using `GET /v1/tasks/{id}`'s latest checkpoint as if
it necessarily carried the requested effect intent. On a legitimate
additional progress update, Agent B falsely rejected the existing intent.

The narrow tenant-authorized read endpoint
`GET /v1/tasks/{task_id}/effects/{effect_key}/intent` now returns the
**original checkpoint matching the effect key and request SHA-256**, plus
the immutable stored effect request, through
`DurableTaskStore.get_effect_intent` and
`MemoryClient.get_task_effect_intent`.

The Producer's HTTP gateway and Agent B now independently resolve the
same effect identity instead of relying on mutable task progress ordering.
This endpoint returns a database record, NOT physical execution
attestation or external authority. Nonexistent and cross-tenant entries
must return 404. The integration test advances task progress after intent
creation, then performs the existing revoke / restore / lost ACK and Agent B
recovery scenarios.
