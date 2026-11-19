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

The current tests cover matching-existing-workflow recovery, conflicting-memo rejection, and process-level SIGKILL at the claim and Temporal-start boundaries. DA-04..DA-06 remain separate acceptance work.

## Required fault matrix

| ID | Kill point | Durable state before kill | Recovery requirement |
|---|---|---|---|
| DA-01 | Before claim transaction commits | Dispatch row remains pending; no committed owner/lease | A replacement Dispatcher claims normally; no phantom lease or skipped task |
| DA-02 | Immediately after claim commits, before Temporal start | Row is dispatching under epoch N; no Temporal execution exists | After lease expiry, replacement claims epoch N+1, starts the same deterministic Workflow ID, and records its Run ID |
| DA-03 | After Temporal accepts start, before PostgreSQL acknowledgement | Row is dispatching under epoch N; exactly one matching Temporal execution exists | Replacement claims epoch N+1, detects and validates the existing execution, records the original Run ID, and does not create another execution |
| DA-04 | During/after stale acknowledgement race | A newer dispatcher owns epoch N+1 | Epoch N acknowledgement is rejected; it cannot overwrite owner, status, or Run ID |
| DA-05 | Existing Workflow ID has wrong memo/type/task queue | Conflicting Temporal execution exists | Dispatcher fails closed; it must not acknowledge the dispatch as started |
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

## Release gate

P0-A remains **NOT ACCEPTED** until DA-01 through DA-06 pass on the same candidate HEAD with real process termination, PostgreSQL read-back, and Temporal read-back. Existing unit tests or the Worker-only SIGKILL test do not satisfy this gate.

P0-B Worker SIGKILL, P0-C provider UNKNOWN reconciliation, P1-A 1,000-task load, and P1-B clean-room Python SDK onboarding remain separate gates.
