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


## Seventh-round explicit cross-ledger release gate

The new `effect_reconciliation.py` is **read-only** and compares tenant-scoped
AMC `memory_task_effects` records, request SHA-256 and external Provider
receipts. It never mutates task/effect status or infers success from a
missing Provider response.

The real PostgreSQL process test intentionally checks the present weakness:
the synthetic Agent B **does not** register its Provider effect through AMC's
fenced checkpoint/outbox contract. The Provider can prove one execution,
but AMC returns `AMC_INTENT_MISSING`, and `safe_for_automatic_completion`
remains `false`. This is an explicit negative control, not a regression to
ignore or a reason to counterfeit a committed effect. Cross-ledger consistency
does not itself authenticate third-party receipts.

Product release still requires a properly leased/fenced Worker to create the
effect intent and outbox **before** dispatch, then an authorized reconciliation
transition based on independently verified Provider evidence. Until this is
implemented and tested, the observer must remain fail-closed.
