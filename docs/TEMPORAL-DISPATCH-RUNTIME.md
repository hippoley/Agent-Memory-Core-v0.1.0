# PostgreSQL → Temporal durable dispatch runtime

## Implemented in this branch

- Task creation and `memory_temporal_dispatches` insertion share one PostgreSQL transaction.
- The independent `agent_memory_core.temporal_dispatcher` process claims rows using PostgreSQL leases and monotonically increasing fencing epochs.
- Workflow IDs are persisted before dispatch. A Temporal start is attempted outside the database transaction.
- The starter writes an immutable `agent_memory_task_identity` memo. On `WorkflowExecutionAlreadyStartedError`, the dispatcher checks Workflow ID, workflow type, task queue and memo identity before treating the existing execution as the result of an earlier ambiguous start.
- The dispatcher records the Temporal Run ID only through the current PostgreSQL lease owner and epoch.
- `DurableTaskWorkflow` exposes identity and progress queries and terminal completion/failure signals. The Temporal worker registers it alongside the existing memory-verification workflow.
- The extended Compose profile runs the dispatcher and Temporal worker as separate processes.

## Important boundary

This is the **dispatch and orchestration-identity slice**, not yet the complete task-execution slice. `DurableTaskWorkflow` intentionally remains open until a task runner signals a terminal result. It does not mark the PostgreSQL task completed and does not itself execute a provider effect. This avoids falsely equating “Temporal accepted a workflow” with “business task completed”.

The existing `DurableTaskWorker` remains the PostgreSQL lease/fencing authority for business handlers. The next integration must connect one explicitly registered task handler to the Temporal workflow without allowing two independent consumers to claim the same task concurrently. It must also signal completion only after `DurableTaskLeaseStore.finish` succeeds and the effect ledger has no unresolved effects.

## Local operation

The extended profile starts the independent dispatcher automatically:

```sh
docker compose --profile extended -f compose.yaml -f compose.temporal.yaml up --build
```

Useful environment variables:

- `MEMORY_TEMPORAL_ADDRESS` (default `localhost:7233`)
- `MEMORY_TEMPORAL_NAMESPACE` (default `default`)
- `MEMORY_TEMPORAL_TASK_QUEUE` (default `agent-memory-core`)
- `MEMORY_TEMPORAL_DISPATCH_LEASE_SECONDS` (default `60`)
- `MEMORY_TEMPORAL_DISPATCH_POLL_SECONDS` (default `2`)

Do not use the local Temporal dev server or SQLite-backed Compose profile as a production deployment.

## Required next failure tests

1. Kill the dispatcher after PostgreSQL claim but before Temporal start; after lease expiry another dispatcher must start the same Workflow ID.
2. Kill the dispatcher after Temporal accepts the Workflow but before PostgreSQL acknowledgement; the next dispatcher must validate the existing execution memo and record its Run ID.
3. Run two dispatchers concurrently; only one current lease epoch may acknowledge.
4. Pre-create a conflicting Temporal execution under the expected ID with a different memo or task queue; dispatch must fail closed.
5. Verify Temporal completion cannot set PostgreSQL business status to completed while an effect is `requested`, `executing`, `unknown` or `reconciling`.

A passing unit test or successful Workflow start is not evidence that these process-crash and provider-effect guarantees have been met.
