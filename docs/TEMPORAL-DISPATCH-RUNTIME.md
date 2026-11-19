# PostgreSQL → Temporal durable dispatch runtime

## Implemented in this branch

- Task creation and `memory_temporal_dispatches` insertion share one PostgreSQL transaction.
- The independent `agent_memory_core.temporal_dispatcher` process claims rows using PostgreSQL leases and monotonically increasing fencing epochs.
- Workflow IDs are persisted before dispatch. A Temporal start is attempted outside the database transaction.
- The starter writes an immutable `agent_memory_task_identity` memo. On `WorkflowExecutionAlreadyStartedError`, the dispatcher checks Workflow ID, workflow type, task queue and memo identity before treating the existing execution as the result of an earlier ambiguous start.
- The dispatcher records the Temporal Run ID only through the current PostgreSQL lease owner and epoch.
- `DurableTaskWorkflow` exposes identity and progress queries, then invokes the registered `execute_durable_task` Activity.
- The Activity verifies the tenant/task/workflow/type tuple against PostgreSQL and runs the existing `DurableTaskWorker` against that exact task ID. The handler commits through the current Worker Lease/Epoch and Effect Ledger APIs; Temporal heartbeat and PostgreSQL lease heartbeat remain separate.
- The Temporal Workflow only reports completion after the Activity reads back a completed PostgreSQL task. A PostgreSQL task already completed after a lost Activity acknowledgement is returned as a replay, not executed again.
- The extended Compose profile runs the dispatcher and Temporal worker as separate processes.

## Important boundary

This is now a **single-task Temporal → PostgreSQL Worker vertical slice** using the packaged example handler. It demonstrates a real handler invocation, three checkpoint commits, fenced task completion and independent PostgreSQL read-back. The example handler is a checkpointing demonstration, not a production business integration or proof of third-party provider behavior.

The dispatcher start/ack SIGKILL boundaries and provider-specific idempotency/reconciliation remain separate acceptance items. The Activity's task lease is the business-state fence; Temporal's Activity retry is not allowed to bypass it. A second Worker may claim the same task only after PostgreSQL lease expiry, and a completed task is read back rather than executed again.

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
5. Start the same Workflow ID twice and verify the second Dispatcher validates the existing memo and returns the original Run ID.
6. Run the real example task end-to-end through Temporal Activity → targeted PostgreSQL lease → checkpoints → fenced completion.
5. Verify Temporal completion cannot set PostgreSQL business status to completed while an effect is `requested`, `executing`, `unknown` or `reconciling`.

A passing unit test or successful Workflow start is not evidence that these process-crash and provider-effect guarantees have been met.
