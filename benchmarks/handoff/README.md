# Agent Handoff Benchmark · observational gate (v0)

**Product objective:** *Make Agent Work Survive the Agent.*

This directory starts the **external outcome evaluator** for US-C01. It is not a
completed benchmark and contains **no measured Mem0 vs AMC claims**.

## Why this exists

A successful `accept_handoff` response proves a validated transfer, not
successful continuation. Similarly, recovered Temporal Workflow state does not
prove an external provider executed only once. We evaluate independently
observed results rather than trusting an Agent's own `completed` message.

The test must use the **same seeded task, evidence, permissions, deadline and
model/runtime configuration** in three isolated arms:

1. `history_replay`: new Agent B re-reads A's raw original record.
2. `mem0`: new Agent B receives memory retrieved through actual Mem0 APIs.
3. `amc`: new Agent B receives AMC PreparedContext and a verified handoff.

All three must run independently, in a fresh process without A's state.
Any actual Mem0 testing must pin package version and retrieval settings; a
hand-coded faux Mem0 implementation is not a legitimate baseline.

## Ground truth and observation format

`cases.jsonl` contains evaluator-owned facts and actual provider effect
expectations, for example:

```json
{"case_id":"supplier-01","required_facts":{"payment_period":"quarterly","requires_approval":true},"expected_effects":["supplier-approval-request"],"forbidden_effects":["supplier-payment"]}
```

`traces.jsonl` contains one independently observed run per (case, arm):

```json
{"case_id":"supplier-01","arm":"amc","observer":"independent","observation_complete":true,"unresolved_unknown":false,"observed_facts":{"payment_period":"quarterly","requires_approval":true},"observed_effects":[{"effect_key":"supplier-approval-request"}],"elapsed_ms":1200,"context_tokens":500}
```

These are **schema examples only**, not real results. The scorer trusts the
supplied observer file; the `observer` string is **not cryptographic proof**.
For a publishable experiment, an isolated observer must derive facts from
canonical final state and provider actions from an **independent provider
ledger**, then sign/hash and archive the raw traces, seed, code SHA, model
configuration and timestamps. Agents must not write observer traces directly.

```bash
python benchmarks/handoff/score.py --cases /path/to/cases.jsonl --traces /path/to/traces.jsonl --output /path/to/report.json
python -m pytest -q tests/test_handoff_benchmark.py
```

Missing arms or observations fail closed. Any duplicate provider effect,
unresolved UNKNOWN, unsafe extra effect or missing required fact makes the
**whole task fail**, even when the context pack is well-formed. Latency and
context size are reported separately, never allowed to hide a safety failure.

## Release conditions before any 9.5/10 claim

- 20+ evaluator-owned tasks across model-switch, session/process crash,
  stale/revoked authorization, corrected fact, checkpoint mutation,
  UNKNOWN effect and repeated accept.
- At least one **real** end-to-end A->B completion through each arm; this
  repository's scorer does **not** execute agents.
- Authenticated per-Agent principals and authorization enforcement, not just
  caller supplied `target_agent` labels.
- Fault-injected provider ledger shows **no double execution** after crashes.
- Same environment, blinded case seeds, published raw traces and statistical
  uncertainty, not cherry-picked successes.
- Independent developer can clone public `main`, install and reproduce the
  workflow and results without author assistance (issue #57).

Only when those gates pass should observed task-success, context loss, side
effect duplication and real cost/latency be compared and an updated score
assigned. No target number, including 9.5, can be established by implementation
alone.
