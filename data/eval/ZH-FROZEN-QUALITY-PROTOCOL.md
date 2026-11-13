# Chinese memory quality · frozen evaluation v1

**Status:** evaluation infrastructure + 12-case adversarial regression dataset. **Not** a completed real-model benchmark, and not a statistically representative accuracy claim.

This evaluation complements—not replaces—the original [200-example home-zh set](gold_home_zh.jsonl). The original dataset remains untouched, including the dated historical report. Rewriting the old report after code changes would destroy the baseline.

## What is actually scored

| Stage | Ground truth | Metric | What it does NOT establish |
|---|---|---|---|
| **Fact extraction** | Per-turn `subject_id,predicate,value,operation` | Micro P/R/F1 with exact structured values; RETRACT by operation+scope | Correct database write or long-term state |
| **Belief revision** | All canonical active claims at end of story | Whole-state exact match | Historical proof / concurrency if not observed separately |
| **Retrieval** | Specific `subject_id + predicate + value` facts | Exact Recall@5, MRR@5, precision; negative-query hygiene | Recall based on just matching a room |
| **Answer** | Curated acceptable strings, including abstentions | Exact-normalized answer accuracy and abstention accuracy | Semantic equivalence outside listed answers or hallucination proof |

A query with zero relevant facts contributes to **negative-query hygiene**, not to an artificial perfect Recall@5. The answer grader is deliberately deterministic; use a separately versioned adjudication protocol for ambiguous free-form answers.

## Two frozen corpora, different purposes

1. **200 cases**: existing single-utterance rule-contract test, five groups (A/B/C/D/E); historical baseline 2026-11-12. The old evaluation script may soft-forget tenant data and must run only on an isolated, disposable deployment.
2. **12 cases**: this new multi-turn adversarial frozen `zh_multiturn_frozen_v1.jsonl`, designed to catch context errors, explicit retractions, ambiguity, stale context, cross-predicate collisions, out-of-vocabulary facts, and unsupported inferences. The 12 cases are a **regression starter set, not a representative sample**. Expand by creating `v2`, not by silently editing v1 after scores are published.

## First: verify corpus hash and scorer

```bash
python scripts/eval/score_zh_frozen.py --manifest
pytest -q tests/test_zh_frozen_evaluator.py
```

The JSON output from `--manifest` is the exact SHA-256 of the committed JSONL bytes. Every result file must declare that hash and include **all** cases. No guessing missing predictions.

## Runnable real rule-extractor ablation (extraction ONLY)

```bash
python scripts/eval/diagnose_zh_frozen_rules.py --out /tmp/zh-rules-no-context.json
python scripts/eval/diagnose_zh_frozen_rules.py --context --out /tmp/zh-rules-context.json
```

This imports and executes `RuleFactExtractor.from_preset("home-zh")` on the real source corpus. It **does not use a database, retriever, or answer model**, and prints its actual extraction F1 only. The output is **not** compatible with a full four-stage comparison because those downstream stages were not run.

## Full four-stage comparison

Run each frozen story in a **clean isolated tenant** with a fixed commit SHA, extraction provider, embedding model, prompt, temperature and search limits. Do not use the regular demo tenant. Capture actual per-turn extractor output, post-write canonical state, real search hits (top 5 in rank order), and real answer model output.

The prediction artifact must have the following shape (illustrative schema; **this is not an actual model result**):

```json
{
  "dataset_sha256": "COPY_EXACT_VALUE_FROM_MANIFEST",
  "configuration": {
    "name": "rule-context-off-aliases-off",
    "extractor": "rule:home-zh",
    "context_enabled": false,
    "aliases_enabled": false,
    "answer_model": "local-model-id-or-none"
  },
  "cases": [
    {
      "id": "zh01",
      "extracted": [
        {
          "turn": 0,
          "subject_id": "主卧",
          "predicate": "temperature_preference",
          "operation": "UPSERT",
          "value": {"celsius": 24}
        }
      ],
      "current": [],
      "retrieved": [],
      "answer": "UNKNOWN"
    }
  ]
}
```

A production prediction file **must contain all twelve cases with complete real stage outputs**; the abbreviated snippet above will fail validation.

```bash
python scripts/eval/score_zh_frozen.py --predictions /tmp/real-run-a.json --out /tmp/report-a.json
python scripts/eval/score_zh_frozen.py --predictions /tmp/real-run-b.json --out /tmp/report-b.json
python scripts/eval/compare_zh_frozen.py --a /tmp/report-a.json --b /tmp/report-b.json --out /tmp/paired-delta.json
```

### Experiment matrix

| Experiment | Extractor | Prior context | Predicate aliases | Answer model |
|---|---|---|---|---|
| A | Rule home-zh | Off | Off | Fixed |
| B | Rule home-zh | On | Off | Same as A |
| C | Rule home-zh | Off | On | Same as A |
| D | Rule home-zh | On | On | Same as A |
| E | Actual local model | Off | Off | Same fixed evaluator |
| F | Actual local model | On | On | Same fixed evaluator |

Keep **dataset, PostgreSQL schema/image, search limit, model parameters and scoring version** pinned across comparisons. Record model names, weights/quantization, prompts, tool invocation mode and actual hardware in the run artifact. If a provider cannot consume `context_turns`, report that unsupported condition; do not silently route it through the rule extractor.

### Known limitations and blocking issues

- An automated four-stage **capture runner** for disposable real HTTP tenants and model-generated answers has not yet been built in this PR. The strict scorer expects actual stage output files. Do not pass fixture-generated values as real model results.
- Some gold items intentionally enforce abstention for ambiguous or stale context. They are editorial judgments, not a universal truth about all utterances.
- Exact accepted-answer lists may reject valid paraphrases; the next revision should include a blinded human review of disputed responses rather than changing gold after viewing model output.
- The current 12-case benchmark is too small for a confidence interval to justify 90%+ generalized quality claims.
- The original 200-case scoring script currently has a weaker retrieval criterion (correct subject rather than fact). Preserve its results as historical **subject-level recall**, never relabel them as fact-level retrieval.

## Release gate

The milestone is **not** '12 cases passed in CI'. It is a new signed-off evaluation report with real captures from a fresh tenant and a fixed local model, a frozen gold hash, per-case errors, reproducible commands, and at least one independent rerun on a clean instance.
