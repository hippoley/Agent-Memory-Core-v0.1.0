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
python benchmarks/handoff/isolated_run.py observe --case-id supplier-01 --subject-id SUBJECT_ID --db /tmp/amc-us-c01-provider.sqlite --elapsed-ms MEASURED_MILLISECONDS --context-tokens MEASURED_TOKENS
```

The observe subcommand emits one JSON trace for the `amc` arm. The timing/token values must be measured externally; do not enter estimates and present them as benchmark measurements. Feed a complete three-arm set to `score.py` only after **real** history replay and pinned Mem0 adapters have produced corresponding independent observations.

## Demonstrated boundary vs remaining gate

- The deterministic policy engine refuses incomplete, ambiguous or unsafe facts.
- The standalone provider journals every HTTP attempt and commits its simulated business effect only once per request id.
- A new Agent B process must derive its decision from accepted AMC payload and persists the final decision through the public API.
- The observer reads canonical memory state and independent provider SQLite, not Agent B's stdout.
- **Still missing:** real model-driven comprehension, authenticated per-Agent delegation, robust external observer identity/attestation, crash injection at each commit boundary, an authenticated production provider, and full three-arm comparative runs.
- The synthetic HTTP provider illustrates a local deduplication mechanism, not a universal exactly-once guarantee. Do not confuse the provider's committed event with AMC's own durable effect ledger.
- Never publish a 9.5/10 score based on this example alone.

Run focused regression with `python -m pytest -q tests/test_handoff_isolated_run.py tests/test_handoff_benchmark.py`.
