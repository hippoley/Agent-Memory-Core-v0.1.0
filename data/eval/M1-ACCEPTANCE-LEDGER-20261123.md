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
