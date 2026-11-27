# US-C02 · The final transition must recheck creator authorization

The strict `continuity.authorized-effect.v1` profile formerly checked current
creator write membership in `WorkerContext.assert_lease()`, but the public
`POST /v1/tasks/{task_id}/status` endpoint calls
`DurableTaskLeaseStore.finish()` directly. A Worker with `tasks:execute`
and a live lease could bypass the in-process authorization gate and claim a
completed result after the creator was downgraded or revoked.

The **same canonical SQL gate** now lives in
`worker_identity.assert_current_task_creator_write_authority` and is called
both from `WorkerContext` and from the database transaction which makes
`status=completed` durable. The task row is locked before the authorization
and required-effect checks; an unauthorized attempt raises `LeaseConflict`
before any completed status or outbox event is written.

Real PostgreSQL regression extends `test_creator_revocation_blocks_db_worker_before_handler_and_effect`:
under a live worker epoch, downgrade task creator to reader and call
`DurableTaskLeaseStore.finish(status="completed")` directly, requiring a
specific authorization rejection and observing status remains running.

Important limits:
- This protects **only opted-in strict task types**; prior task types are not
  silently upgraded.
- This does NOT grant delegated Agent B execution, device capabilities, or
  prove that a remote provider has honored a revoke.
- A separate authorized worker may still perform read-only reconciliation
  of an ambiguous effect when designed to do so, but execution is forbidden.
