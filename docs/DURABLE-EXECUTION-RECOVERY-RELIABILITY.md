# Durable Execution · Recovery & Reliability
## P0-A Dispatcher SIGKILL acceptance contract

**Baseline:** `main` at `e3be739d4dfcf0d7135ac81c313bd8507ee831cf`  
**Working branch:** `feat/durable-execution-recovery-reliability-20261119`

This document defines the required process-level acceptance. It is not evidence that the scenarios have passed.

## Scope

Exercise the production dispatcher entrypoint against a real PostgreSQL database and a real Temporal service. The dispatcher process must be a separate OS process and must be terminated with SIGKILL at deterministic boundaries. Tests must inspect PostgreSQL and Temporal independently after recovery.

The current implementation already has:
- a PostgreSQL dispatch ledger with `FOR UPDATE SKIP LOCKED`, lease expiry and monotonically increasing fencing epoch;
- a deterministic Workflow ID derived from tenant + task;
- Temporal start outside the PostgreSQL transaction;
- immutable task identity in the Workflow memo;
- an epoch-fenced PostgreSQL acknowledgement;
- recovery validation of Workflow ID, workflow type, task queue and memo identity.

The current candidate includes process-level SIGKILL coverage for DA-01..DA-03; PostgreSQL stale-epoch acknowledgement and two-connection claim-race tests for DA-04/DA-06; and Temporal identity rejection for conflicting memo, workflow type, and task queue under DA-05. These describe implemented test coverage, not a claim that the complete release gate has passed on this HEAD.

## Required fault matrix

| ID | Kill point | Durable state before kill | Recovery requirement |
|---|---|---|---|
| DA-01 | Before claim transaction commits | Dispatch row remains pending; no committed owner/lease | A replacement Dispatcher claims normally; no phantom lease or skipped task |
| DA-02 | Immediately after claim commits, before Temporal start | Row is dispatching under epoch N; no Temporal execution exists | After lease expiry, replacement claims epoch N+1, starts the same deterministic Workflow ID, and records its Run ID |
| DA-03 | After Temporal accepts start, before PostgreSQL acknowledgement | Row is dispatching under epoch N; exactly one matching Temporal execution exists | Replacement claims epoch N+1, detects and validates the existing execution, records the original Run ID, and does not create another execution |
| DA-04 | During/after stale acknowledgement race | A newer dispatcher owns epoch N+1 | Epoch N acknowledgement is rejected; it cannot overwrite owner, status, or Run ID |
| DA-05 | Existing Workflow ID has wrong memo/type/task queue | Conflicting Temporal execution exists | Dispatcher fails closed for each conflict independently; it must not acknowledge the dispatch as started |
| DA-06 | Two Dispatchers claim concurrently | One pending dispatch row | Exactly one current lease owner/epoch is returned; no double claim |

## Assertions (all required)

1. **PostgreSQL ledger:** inspect status, owner, epoch, attempts, lease expiry, and Temporal Run ID directly after each process boundary.
2. **Temporal history:** list/describe executions by the deterministic Workflow ID; verify workflow type, task queue, memo identity, and Run ID.
3. **No duplicate execution:** DA-03 must show one Workflow execution identity and the recovered Run ID must equal the original Run ID.
4. **Fencing:** an old owner/epoch cannot acknowledge after a newer claim.
5. **No false success:** process exit, timeout, or a Temporal client exception is not itself proof that a Workflow did or did not start.
6. **Evidence:** save process exit signal, timestamps, PostgreSQL snapshots, Temporal descriptions/history, and final assertions as machine-readable artifacts.

## Test harness requirements

- Use PostgreSQL 16 and a real Temporal service in the integration job.
- Start Dispatcher A and Dispatcher B as separate subprocesses.
- Add deterministic, test-only synchronization at the exact boundaries (before claim commit; claim committed; Temporal start accepted; before acknowledgement). Do not use arbitrary sleeps as the synchronization mechanism.
- Send SIGKILL to Dispatcher A; verify its exit status is signal 9.
- Use short, bounded test leases in the isolated test database; do not weaken production defaults.
- Always clean up subprocesses, Temporal environment, and test rows in a `finally` block.
- Run the focused fault matrix locally before the full regression suite; run CI once against the final candidate commit.
- DA-05 must have independent cases for memo, workflow type, and task queue. A test that only asserts a generic failure without inspecting the existing Temporal execution is insufficient.

## Release gate

P0-A remains **NOT ACCEPTED** until DA-01 through DA-06 pass on the same candidate HEAD with real process termination, PostgreSQL read-back, and Temporal read-back. Existing unit tests or the Worker-only SIGKILL test do not satisfy this gate.

P0-B Worker SIGKILL, P0-C provider UNKNOWN reconciliation, P1-A 1,000-task load, and P1-B clean-room Python SDK onboarding remain separate gates.

## Live PostgreSQL ↔ Temporal reconciliation (read-only)

The candidate now exposes `inspect_temporal_dispatches(conn, client, tenant_id=...)` and the `amc-temporal-reconcile` command. It reads a tenant-scoped set of durable tasks, dispatch ledger rows, effect states, and the corresponding Temporal Workflow descriptions. It does not update either system.

Example:

```sh
amc-temporal-reconcile --tenant-id tenant-example --limit 100
```

The JSON report distinguishes:
- `TEMPORAL_COMPLETED_POSTGRES_NONTERMINAL`: Temporal is complete while PostgreSQL business state is not terminal.
- `TEMPORAL_STOPPED_POSTGRES_NONTERMINAL` / `TEMPORAL_FAILED_POSTGRES_NONTERMINAL`: Temporal stopped or failed; business outcome still requires evidence.
- `TEMPORAL_EXECUTION_MISSING`: Temporal explicitly reports the deterministic Workflow ID as missing.
- `TEMPORAL_LOOKUP_FAILED`: Temporal could not be queried; existence is unknown, not missing.
- `TEMPORAL_EXISTS_DISPATCH_UNACKNOWLEDGED`: Temporal accepted a matching Workflow, but PostgreSQL is still dispatching and has not recorded the Run ID.
- `TEMPORAL_EXISTS_DISPATCH_PENDING`: a matching Temporal Workflow exists while the PostgreSQL dispatch ledger is still pending.
- `POSTGRES_DISPATCH_STARTED_TEMPORAL_MISSING`: PostgreSQL records a started dispatch but Temporal explicitly reports the Workflow ID missing.
- `TEMPORAL_IDENTITY_CONFLICT`: Workflow type, task queue, Workflow ID, or immutable memo differs from PostgreSQL.
- `TEMPORAL_RUN_ID_DIVERGENCE`: the discovered Temporal Run ID differs from the PostgreSQL dispatch ledger.
- `PG_COMPLETED_WITH_UNRESOLVED_EFFECTS`: PostgreSQL says completed while an external effect remains requested, executing, unknown, or reconciling.

Every report item sets `safe_for_automatic_repair=false`. A read timeout is never classified as a missing Workflow. This command is an observation and evidence-gathering boundary, not a repair mechanism.

The adapter tests use contract doubles for database and Temporal boundaries. The real PostgreSQL 16 + Temporal service integration run remains required before claiming live-environment acceptance.

## CI evidence receipts (DA-01..DA-03)

The extended Temporal integration workflow sets `MEMORY_DA_EVIDENCE_DIR` and, after the three real process-level SIGKILL scenarios, requires the three JSON receipts. Successful workflow runs retain them for 30 days as the `dispatcher-sigkill-receipts` artifact. Each record contains scenario, UTC boundary timestamps, the actual negative SIGKILL exit code, replacement exit code, selected pre/post PostgreSQL ledger fields, the independently described Temporal Workflow/Run identity, and explicit assertion outcomes. Credentials, DSNs and process environments are not written to receipts.

**Evidence limitation:** these receipts cover DA-01..DA-03 and Temporal **describe** readback only, not a complete Temporal execution history export. DA-04..DA-06 are exercised in other tests but have no analogous retained machine-readable receipts here. This addition does not alone satisfy the entire P0-A release gate, and only a successful workflow run on the exact resulting candidate HEAD would establish acceptance of the new receipt path. Neither a passing workflow nor an emitted receipt substitutes for final release review.

## Expanded proof contract (candidate, subject to exact-HEAD CI)

In addition to the three process-level SIGKILL receipts, the extended Temporal workflow now runs the real-PostgreSQL stale-Epoch/dual-claim tests and the real-Temporal conflicting-memo/task-queue/workflow-type tests. The acceptance check requires **eight** JSON receipts: DA-01, DA-02, DA-03, DA-04, DA-05-memo, DA-05-task-queue, DA-05-workflow-type, and DA-06. DA-04 captures database state before and after the rejected stale acknowledgement; DA-06 captures both independent connection claim outcomes plus the resulting persisted owner and Epoch; DA-05 captures independent before/after Temporal descriptions for each conflict and asserts an unchanged Run ID/type/queue.

These are separate real-service tests rather than one end-to-end process-level SIGKILL matrix for all scenarios. **Do not equate the DA-04..DA-06 receipts with killed-Dispatcher-process tests.** There is still no exported full Temporal event history, and the test-only failpoints, correctness of the identity evidence and cross-system state must be reviewed before marking the complete P0-A release gate accepted. The existence of source code or a green unrelated test is not acceptance; check the exact candidate HEAD and the uploaded eight-file artifact.

## Actual Temporal event-history evidence (candidate)

The DA-01..DA-03 SIGKILL acceptance now calls `WorkflowHandle.fetch_history_events()` with the recovered **Run ID**, separately from the PostgreSQL dispatch ledger. It requires a nonempty, unique-ID event sequence starting with `EVENT_TYPE_WORKFLOW_EXECUTION_STARTED`, and retains event IDs and types in the JSON receipt. The workflow also rejects receipts lacking this event history. A Temporal describe response alone no longer fulfills this history check.

This is an **event-ID/type evidence summary**, not a full serialized Temporal history payload: it does not preserve event attributes, payloads, all memo details or a cryptographic proof of server origin. Actual service failure must fail closed. Acceptance of the feature requires the current-head extended Temporal CI to pass and publish the updated artifacts, followed by mainline integration and review of production reliability implications.
