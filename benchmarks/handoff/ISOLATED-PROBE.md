# US-C01 isolated process probe (experimental)

The first product benchmark probe is now executable with **real AMC REST/SDK** and a **separate provider HTTP process**. It is not an LLM comprehension test and is not a comparative Mem0 result.

## Prerequisite

Start AMC with its documented PostgreSQL API profile. Export:
`MEMORY_API_URL`, `MEMORY_API_KEY`, and `MEMORY_TENANT_ID`.
Use a disposable tenant. Never publish credentials, case records or raw customer data.

Agent A seeds the task and two canonical source-linked facts in its **own process**, freezes the handoff and exits:

```bash
python benchmarks/handoff/isolated_run.py agent-a --subject-id supplier-probe-001
```

Record the returned `handoff_id` and `subject_id` (and estimate). The two source-linked facts are `payment_period=quarterly` and `requires_approval=true`. The same subject must be used when running Agent B. No raw transcript or Agent-A process memory is passed to B. This is a clean *process exit* test, not yet a forced SIGKILL race test.

## Three separate processes

Provider process (different terminal):

```bash
python benchmarks/handoff/isolated_run.py provider --db /tmp/amc-us-c01-provider.sqlite
```

Agent B process, using only public AMC + provider HTTP APIs:

```bash
python benchmarks/handoff/isolated_run.py agent-b --handoff-id HANDOFF_ID --subject-id SUBJECT_ID
```

After Agent B exits, read the actual provider SQLite event ledger and canonical AMC output from a fresh observer process:

```bash
python benchmarks/handoff/isolated_run.py observe --case-id supplier-01 --subject-id SUBJECT_ID --handoff-id HANDOFF_ID --db /tmp/amc-us-c01-provider.sqlite --elapsed-ms MEASURED_MILLISECONDS --context-tokens MEASURED_TOKENS
```

The observe subcommand emits a **provisional, intentionally fail-closed** JSON trace for the `amc` arm: it scopes provider events to the handoff, but marks `observation_complete=false` and `unresolved_unknown=true` until an independent task/effect reconciliation is verified. It must **not** be counted as a passing benchmark case. The timing/token values must be measured externally; do not enter estimates and present them as benchmark measurements. Feed a complete three-arm set to `score.py` only after **real** history replay and pinned Mem0 adapters have produced corresponding independent observations.

## Demonstrated boundary vs remaining gate

- The deterministic policy engine refuses incomplete, ambiguous or unsafe facts.
- The standalone provider journals every HTTP attempt and commits its simulated business effect only once per request id.
- A new Agent B process must derive its decision from accepted AMC payload and persists the final decision through the public API.
- The observer reads canonical memory state and independent provider SQLite, not Agent B's stdout.
- **Still missing:** real model-driven comprehension, authenticated per-Agent delegation, robust external observer identity/attestation, crash injection at each commit boundary, an authenticated production provider, and full three-arm comparative runs.
- The synthetic HTTP provider illustrates a local deduplication mechanism, not a universal exactly-once guarantee. Do not confuse the provider's committed event with AMC's own durable effect ledger.
- Never publish a 9.5/10 score based on this example alone.

Run focused regression with `python -m pytest -q tests/test_handoff_isolated_run.py tests/test_handoff_benchmark.py`.


## Deterministic crash-window integration (CI)

The real PostgreSQL/HTTP acceptance test now exercises two separate cases
against a disposable tenant:

- `clean-restart`: agent A and B run in independent processes; a second B
  replays the same handoff; the provider commits only one effect.
- `sigkill-after-provider-commit`: the provider persists the first effect
  transaction to its own SQLite ledger and then **holds its HTTP response**.
  The test verifies that the effect was committed, sends a real SIGKILL to B,
  unblocks the provider, starts a fresh B process, and independently checks
  one provider POST attempt / one committed action / one canonical AMC decision.

Invoke the exact integration test from a running PostgreSQL development
stack with `MEMORY_TEST_DSN` configured:

```bash
python -m pytest -q -rs tests/test_handoff_real_process.py
```

This test is also included in `temporal-memory-integration` CI.
The held response is a **testing fault injection**, not a production provider
capability. Importantly, AMC still does not record an independently
reconciled cross-provider effect receipt or guarantee exactly-once semantics
for non-idempotent tools. The standalone observer continues to fail closed
until that distinct product requirement is actually implemented and verified.


## Sixth-round provider reconciliation contract (controlled only)

On fresh startup, Agent B asks the independent HTTP Provider for
`GET /effects/{request_id}` **before** attempting any further action. It
compares the returned `request_id`, effect key and business payload with
its independently reconstructed accepted handoff. A matching committed
receipt allows canonical AMC continuation *without another POST*. A
mismatching receipt is a hard CONFLICT; a broken Provider connection is
UNKNOWN and aborts the decision. Only a reliable 404 from this specific
idempotent Provider permits a same-key POST, and the caller reads the
receipt again before publishing a completed decision.

This is deliberately *not* AMC-wide reconciliation: the synthetic Provider
owns the receipt, there is no authenticated external receipt signature,
no general cross-provider registry, and no durable `memory_task_effects`
resolution written by this example. Non-idempotent Providers cannot reuse
the 404/POST behavior as a safe generic retry algorithm. The result
observer remains fail-closed and must not be scored as a verified benchmark.


## Eighth-round fenced-intent acceptance (real PostgreSQL)

The earlier negative control at seventh-round HEAD `a0cf4d875377` proved
that Agent B could dispatch an effect without an AMC-owned intent. This is
now a regression test: a B subprocess with no registered intent is refused
**before any Provider POST**.

The real process acceptance test now explicitly orders:

1. Agent A creates the task/claims/handoff and exits.
2. An independent Agent B process accepts the handoff without dispatch.
3. A second Agent B process attempts dispatch with no registered intent;
   it **must fail**, leaving the Provider SQLite ledger empty.
4. A distinct `fenced_worker.py` subprocess claims a lease through the
   existing `DurableTaskLeaseStore` and atomically commits checkpoint,
   effect request digest and Outbox event in PostgreSQL.
5. Agent B confirms the exact current persisted checkpoint via the public
   task read API, then performs the Provider action.
6. The clean-restart and commit-before-response SIGKILL scenarios run.
   Fresh Agent B reads the committed Provider receipt and does not POST again.
7. A read-only cross-ledger check confirms request hash and receipt match,
   but still classifies `MATCHED_RECEIPT_AMC_UNRESOLVED`. It intentionally
   does **not** claim an authorized effect status transition.

The worker is a disposable controlled test harness with PostgreSQL access.
The Agent B client itself uses only the public AMC and Provider HTTP APIs.
This **does not** yet prove production separation of worker credentials,
ownership of provider dispatch by AMC, authenticated evidence, fencing
across the external Provider, or a safe `unknown -> committed` transition.
It must remain Draft until an authorized reconciler is implemented and
tested with negative/conflict/revocation cases.


## Ninth-round existing AMC effect state machine exercise (CI gate)

The controlled integration now exercises the **existing**
`agent_memory_core.durable_effects.DurableTaskEffectStore`, rather than
introducing a second effect ledger or claiming that a read-only cross-ledger
comparison has authority to update state:

1. The separate fenced Worker records the checkpoint, effect intent and
   Outbox transaction, then calls `begin_execution` under its current epoch.
2. Agent B is denied before the recorded intent exists, and otherwise
   performs the same external Provider call. The real SIGKILL test still kills
   B after the Provider commits but before it replies.
3. An independent recovery Worker is started. The test **injects expiry of
   the previous lease in PostgreSQL**, then claims a newer epoch using the
   normal lease machinery.
4. `mark_stale_executions_unknown` fences the old execution;
   `begin_reconciliation` marks the effect as reconciling; the Worker
   independently reads the Provider receipt, checks the exact effect
   identity/payload and SHA-256 intent digest, then calls the existing
   `resolve_reconciliation` transition.
5. An unreadable, absent or conflicting receipt is **not** converted to
   committed; the effect remains UNKNOWN. A matching committed receipt
   allows the new fenced Worker to record COMMITTED and finish the task.
   Agent B does not get this reconciliation privilege and never executes a
   second Provider POST on recovery.

All mutations occur in the existing PostgreSQL transactional effect/task
tables, with Outbox events. The recovery harness's lease-expiry UPDATE is
**test-only** and must not be copied into production code. The Provider is a
synthetic local SQLite+HTTP service, its receipt is not cryptographically
authenticated, and this is not yet a credential-separated production
reconciler or three-arm real-model benchmark. Full latest-HEAD CI must pass
before counting this controlled end-to-end experiment as accepted.


## Tenth round: execution authority separation (controlled probe)

**New release-critical distinction:** an accepted Handoff and a visible
checkpoint do not authorize external execution. The Agent B process has
no Provider executor token and **never issues a Provider POST**. Agent B
may only inspect the current AMC intent and independently query a
committed receipt, then idempotently publish its derived decision.

The controlled Provider requires an explicit `PROVIDER_EFFECT_TOKEN` on
every POST. The test gives that secret **only** to the Provider and
`dispatch_worker.py`, not Agent A/B or the recovery observer. It verifies
that an attempted unauthenticated POST returns 403, and that Agent B
refuses to report success when a receipt does not exist.

`dispatch_worker.py` checks that its Worker ID and fencing epoch
currently own the same AMC task and `EXECUTING` effect, and that the
effect request matches AMC's persisted SHA-256 before transmitting POST.
The after-commit/before-response SIGKILL injection now kills the actual
Provider **executor** process, rather than Agent B. A new fenced
reconciliation process subsequently reads the Provider receipt, marks
the old effect UNKNOWN and then COMMITTED under a newer epoch, and
finishes the task. The independent Agent B finally reads the committed
receipt without issuing another POST.

This still uses a simulated Provider secret, a test-scoped PostgreSQL
Worker identity and forced lease expiry; it is not a production
authentication deployment, not proof that arbitrary providers honor
fencing, and not an attested external receipt.
