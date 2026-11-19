# Standalone Durable Task Worker example

This is a handler module for the independent worker process implemented by
`agent_memory_core.worker_runtime`. It demonstrates checkpoint continuation;
the item loop is intentionally a placeholder, not a production memory
organizer or an exactly-once external-effect example.

## Install and configure

Install the same branch build as the API service, then expose this directory
on `PYTHONPATH`:

```bash
python -m pip install 'agent-memory-core[postgres] @ git+https://github.com/hippoley/Agent-Memory-Core-v0.1.0.git@feat/durable-task-lease-fencing-recovery-20261118'
export PYTHONPATH="$PWD/examples/durable_task_worker:$PYTHONPATH"
export MEMORY_DSN='postgresql://<worker-login>:<secret>@<host>:5432/<database>'
export MEMORY_WORKER_TENANT='<workspace-id>'
export MEMORY_WORKER_HANDLER_MODULE='handlers'
export MEMORY_WORKER_ID='memory-worker-01'
export MEMORY_WORKER_LEASE_SECONDS='30'
export MEMORY_WORKER_POLL_SECONDS='1'
export MEMORY_WORKER_REQUIRE_RLS='1'
python -m agent_memory_core.worker_runtime
```

The database login must be explicitly bound to exactly one tenant by the
operator-installed identity/RLS registry. The runtime fails closed if
`assert_database_tenant` cannot verify that binding. Do not run the worker
with an application-owner or superuser database login.

## Handler contract

Each handler receives `(task, context)`. It may read the last committed
checkpoint from `context.checkpoint` and commit the next checkpoint with
`context.commit_checkpoint(...)`. The context binds every write to the
claimed worker ID and lease epoch. A stale worker receives `LeaseConflict`
and must stop; it must never attempt to force completion.

For external effects, first persist the effect intent atomically with a
checkpoint, then call a provider using a stable idempotency key and reconcile
ambiguous outcomes before recording the effect as confirmed. The worker
runtime does not turn remote side effects into exactly-once operations.
