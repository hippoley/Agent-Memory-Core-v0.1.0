# M1 real-Chinese-memory acceptance ledger · 2026-11-23

**Scope:** Memory quality (extract → canonical revision → retrieval → answer); not execution or Agent provider fencing. This ledger records confirmed source artifacts and explicit missing inputs, not fabricated model evidence.

## Frozen repository anchors

- Baseline `main`: `919428439ee1851cbfb252f4ffee81802d578377`.
- M1 evaluation branch: `feat/memory-m1-multiturn-capture-20261123` (use the exact HEAD reported by GitHub during each evaluation).
- Existing 200 utterance rule gold: `data/eval/gold_home_zh.jsonl`, Git blob `722f82161062ac2367229ff1b098e28a134be058`, recorded SHA-256 `f7aec900c5cebdd61b8c6c0637c9f0e3332451acb1b2a2ab05bd5a187f37453e`.
- Existing 12 multi-turn adversarial stories: `data/eval/zh_multiturn_frozen_v1.jsonl`, Git blob `f95f0fba8156dc5523da4030938829b18f002017`. Obtain SHA-256 using `python scripts/eval/score_zh_frozen.py --manifest`; do not substitute Git blob SHA for content SHA-256.
- Extraction scorer: `scripts/eval/score_zh_frozen.py`, Git blob `e49a7f2f1ddbf3e26bea5779532fba9be2acb997`.
- Historical **rule-only** 200-utterance result (`ZH-200-RULE-REPLAY-20261116.md`): F1 `0.943396`, P `0.980392`, R `0.909091`; out-of-vocabulary coverage `0/30`. This is **not** a real-model, multi-turn or end-to-end result.

## Required experiment profiles — currently NOT executed

| Field | Baseline A | Baseline B |
|---|---|---|
| Code | exact PR #65 HEAD, pin before run | same HEAD |
| Corpus | fixed 12-story SHA-256 | identical |
| Extractor | real local Chinese model, record weights/hash | same weights/hash |
| Answer model | real local model ID, weights/hash | identical |
| Context | 1 previous turn | 3 previous turns |
| Search | identical authenticated search config | identical |
| Sampling | temperature=0 and fixed response cap | identical |
| Runtime | log CPU/GPU, RAM/VRAM, package versions | identical |
| Clean tenant | separate disposable tenants | separate disposable tenants |
| Artifacts | raw predictions, report, per-case errors | same artifacts |

The local model IDs, quantization hashes, actual hardware measurements and final benchmark results have **not been supplied or measured**. Do not fill them with hypothetical values. The 200-case gold is **single-utterance rule-labeled data** and must not be called a human-adjudicated 200-story multi-turn corpus.

## Exact execution requirements

1. Use a disposable loopback AMC HTTP tenant and local extraction / answer models; do not point benchmark cleanup at user or production data.
2. Run `capture_zh_frozen_http.py` with `--extractor model --context --context-window 1` and `3` under otherwise identical configuration, collecting prediction and report JSON.
3. Compare only identical frozen dataset SHA and pinned configuration using `compare_zh_frozen.py`. Record the model digest, quantization and runtime measurements in the run manifest.
4. Capture the four stages for every story. For any missing stage, malformed output, unsupported context or failed service call, mark the run **FAIL/INCOMPLETE** rather than substitute expected gold.
5. Have human reviewers adjudicate a **new** 200+ multi-turn story corpus; freeze its labels before evaluating model predictions. Existing small set is regression-only.

## Release status

- PR #65 corrects a prior single-turn context limitation but has to honor the shared fact contract / API max **three prior turns**.
- Code-level CI success establishes software regression health only.
- **M1 real-model extraction + HTTP four-stage execution remains BLOCKED** until an actual local model runtime and independently adjudicated evaluation corpus are available.

## Executable Story-1 runbook (requires actual local services)

Preconditions: `pip install -e .`, disposable loopback AMC API with an empty dedicated tenant, a pre-installed loopback local extractor and answer model configured with existing AMC provider environment variables, and a fixed search alias configuration. No production credentials or real user data.

```bash
python scripts/eval/score_zh_frozen.py --manifest
# Set your own real local API URL, model and key; do not use cloud endpoints.
# Use a different freshly provisioned tenant for EACH run.
python scripts/eval/capture_zh_frozen_http.py \
  --base-url http://127.0.0.1:8767 --key "$MEMORY_API_KEY" \
  --extractor model --context --context-window 1 --name model-context-1 \
  --answer-url http://127.0.0.1:1234/v1/chat/completions \
  --answer-model "$LOCAL_ANSWER_MODEL" --ack-disposable \
  --predictions /tmp/m1-ctx1.predictions.json \
  --report /tmp/m1-ctx1.report.json \
  --diagnostics /tmp/m1-ctx1.failures.json
# Re-provision an equally configured, never-before-used evaluation tenant before context-3.
python scripts/eval/capture_zh_frozen_http.py \
  --base-url http://127.0.0.1:8767 --key "$MEMORY_API_KEY" \
  --extractor model --context --context-window 3 --name model-context-3 \
  --answer-url http://127.0.0.1:1234/v1/chat/completions \
  --answer-model "$LOCAL_ANSWER_MODEL" --ack-disposable \
  --predictions /tmp/m1-ctx3.predictions.json \
  --report /tmp/m1-ctx3.report.json \
  --diagnostics /tmp/m1-ctx3.failures.json
python scripts/eval/compare_zh_frozen.py \
  --a /tmp/m1-ctx1.report.json --b /tmp/m1-ctx3.report.json \
  --strict-context-pair --out /tmp/m1-paired-delta.json
```

The `--answer-url` is only an example local loopback API and must be replaced with the actual working endpoint. Real model identity, digest, quantization and hardware remain operator-supplied and must be independently recorded; the optional diagnostic output is explanatory, **not** a real-model provenance attestation. The runner refuses nonempty / previously-used tenant histories. If the model or the 200 human-adjudicated multi-turn examples are unavailable, Story 1 must remain **BLOCKED**, even when unit and database CI pass.

A saved prediction file can be re-diagnosed without another costly model run:

```bash
python scripts/eval/diagnose_zh_frozen.py \
  --predictions /tmp/m1-ctx1.predictions.json \
  --require-real-capture --out /tmp/m1-ctx1.rediagnosed.json
```

## M01 extra integrity repairs (2026-11-23)

- **RETRACT scoring semantics:** the capture evaluates `expected_value` (old canonical fact) when present instead of erroneously treating a generic withdrawal reason as the fact being retracted. The original model candidate remains unchanged in the HTTP request; the score still penalizes retractions without a grounded old value.
- **Runtime identity evidence:** real model runs now include a stable `extractor_runtime_identity` configuration field from the extractor's actual response-attestation API. Capture fails if a response model changes mid-run or returns an identity mismatch. OpenAI-compatible backends may not report a model-weight digest; this is *observed response identity*, **not** proof of cryptographic model-weight equivalence.
- **Strict window comparison:** `compare_zh_frozen.py --strict-context-pair` requires matching observed runtime identity, in addition to the declared extractor model, answer model, aliases and all other declared settings. Old reports without runtime identity evidence cannot satisfy this stricter release claim.
- **Unmet pinning:** answer-model weights and independent server executable hashes must still be pinned by the operator. No live model score is claimed merely because runtime identity metadata exists.

## Story isolation correction (2026-11-23)

A soft-forgotten memory is **not deleted**: historical claims/events may remain. Clearing active facts between stories in a single tenant is useful for quick debugging but is not a scientifically independent evaluation. Genuine release-grade runs should pass `--case-keys-file` and use **one fresh, never-used tenant per story**. Each tenant key is checked against the authenticated `tenant_id` returned by `/v1/evaluation/search-config`; the same tenant behind two keys is rejected, as are differing alias configurations.

Make a secure file (outside the repository) containing exactly one unique API key per frozen story, in the identical order as the gold JSONL. The first line must equal the key supplied as `--key`. The file contains credentials; do not commit it or upload it as an artifact.

```bash
chmod 600 /secure/path/m1-story-keys.txt
# Add to BOTH previous capture commands:
#   --case-keys-file /secure/path/m1-story-keys.txt
# For the second comparison run, provision a NEW set of tenant keys,
# retaining the same extractor/answer model and search configuration.
```

The resulting configuration records `case_isolation=fresh_tenant_per_story`; legacy runs without this flag record `shared_tenant_soft_reset`. The strict `diagnose_zh_frozen.py --require-real-capture` proof gate rejects shared-tenant runs. Model performance statistics remain unmeasured until a real local runtime and actual isolated tenants are provisioned.


## Grounded revision paired experiment (new capture capability; not yet executed)

Two **independent fresh-tenant sets** are required: one for context-only and one for context + current canonical memory. Both use the same three prior utterances, local extractor, local answer model, alias configuration, frozen corpus, quantization/runtime identity and sampling. A new tenant key is required for each story in *each* arm. Do not use the same tenant keys twice. Never commit keys.

- **Arm A:** `--extractor model --context --context-window 3` with no grounding flag.
- **Arm B:** identical arguments plus `--ground-with-memory`. Before *each* turn, the client retrieves tenant-scoped current facts using authenticated `POST /v1/search`; the model sees only returned canonical evidence; `guard_retractions()` validates proposals before `POST /v1/memories/process`. Every captured prediction records the searched canonical memory IDs without exposing tenant credentials.
- **Comparison:** after generating both independent reports, run:

```bash
python scripts/eval/compare_zh_frozen.py \
  --a /tmp/m1-context-only.report.json \
  --b /tmp/m1-grounded.report.json \
  --strict-grounding-pair --out /tmp/m1-grounding-delta.json
```

**Scientific interpretation:** This isolates the benefit of adding *retrieved canonical evidence to extraction*, not the benefit of memory versus having no persistent memory at all. Both arms still use AMC to store, search, and answer. It is **not** the future-task/no-memory/summary/AMC experiment US-M02. Search misses may prevent a justified retraction; that is a measured retrieval/grounding failure and must not be silently patched using gold labels. The output reports are actual only after a local model and freshly provisioned isolated tenants have executed. CI proves only the capture/comparison implementation, not real-model gains.
