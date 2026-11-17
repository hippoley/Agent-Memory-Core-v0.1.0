# P0 Chinese hazardous semantics and cross-tenant PostgreSQL attack gate

## Scope and source of evidence

The first REAL Qwen provider execution was performed on the dedicated
`eval/p0-zh-dangerous-live-20261117` branch (never merge the temporary
`push` trigger) in [Actions #44044949730](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/actions/runs/44044949730).
It used the Beijing pay-as-you-go Qwen-compatible endpoint, model `qwen-plus`,
a random isolated tenant, real Memory API, and real disposable PostgreSQL.
Five pre-defined cases passed, including strict per-case model attestation,
canonical claim state, audit history and protective controls.

The output is `evidence/qwen-dangerous-zh.json` and a report-SHA256 manifest,
uploaded as `qwen-real-dangerous-zh-evidence`. The acceptance workflow on
`main` is **manual** (`workflow_dispatch`) to avoid accidental model cost.

| Case | Required evidence |
|---|---|
| negation | Old 28°C fact retired; 26°C current; 28°C absent |
| explicit_retract | Exact monthly-settlement old ID retired; zero active replacements |
| clear_anaphora | Exact known quiet-preference ID retired; no active quiet preference |
| ambiguous_anaphora | Two distinct subjects' active claims and histories unchanged |
| predicate_subject_collision | First supplier updates to quarterly; second's current 6-month ID and history unchanged |

This is a **five-case synthetic smoke suite**, not a statistically valid
error-rate estimate for open-world Chinese. `false_retraction_count: 0`
means zero failures on the defined protected-case checks **in this run only**.
It is not an unrestricted language safety certification.

## Cross-tenant real PostgreSQL adversarial integration

`tests/test_p0_tenant_attack_pg.py` exercises two independent FastAPI app
instances with different fixed server-side tenant IDs and API credentials but
the SAME PostgreSQL integration database. A cannot be accessed using B's
credential; B's valid API cannot browse/search A's memory ID or see A's sources;
B cannot DELETE A's memory ID; and a model-proposed RETRACT containing the
known A ID is rejected when it is not in B's canonical grounding evidence.
Even B's broad subject withdrawal cannot change A.

A complete victim snapshot includes the history rows AND `memory_events`
payloads before and after all attack attempts. This test passed in actual
PostgreSQL integration Actions for commit `51f8efaa7e533f4bbb6eda0223661d00627da123`.

## Remaining limitations

* This validates **two separate single-tenant API instances**, not dynamic
  per-request multi-tenant identity routing in one process.
* The semantic model suite protects only the specified synthetic facts and
  predicates, and lexical/SQL search was used; vector/AGE optional channels
  were disabled in the Qwen workflow.
* No database power-failure injection, signed third-party receipt, statistically
  representative benchmark, or independent hostile operator review is claimed.
* Do not use production databases, customer memories, or Coding Plan keys.

## Reproduction

For integration tests use an isolated `MEMORY_TEST_DSN` and run
`pytest -q -m integration tests/test_p0_tenant_attack_pg.py`.
For billed Qwen model acceptance open Actions, select
`qwen-real-dangerous-zh-acceptance`, run with a regular pay-as-you-go
`DASHSCOPE_API_KEY` Actions secret, and download the resulting
`qwen-real-dangerous-zh-evidence` artifact. No model calls run for PR CI.
