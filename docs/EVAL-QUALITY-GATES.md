# Frozen Chinese Memory Evaluation · strict correction gate

This change fixes a scorer false positive: an extracted `RETRACT` targeting the correct subject/predicate/turn but the **wrong old canonical value** formerly counted as a true positive because the scorer dropped `value` from the event key. That is no longer acceptable.

## What is now scored

1. **Extraction:** the old target value for every `RETRACT` must match the expected value, in addition to operation, subject, predicate and turn. Explanatory markers such as `{"reason":"explicit_withdrawal"}` are not substitutes for canonical old values.
2. **Revision:** final active canonical facts exactly match the frozen expected set.
3. **Retrieval:** the expected fact set matches the actual returned top-five fact set with no extra wrong facts; negative queries must be clean.
4. **Answer:** normalized output equals one of the frozen accepted answers.
5. **End-to-end exact story:** **all four** gates above must pass in the *same case*. It is not an average and cannot be rescued by a lucky final answer after an incorrect retraction.

The new report field is `end_to_end.exact_story_accuracy`. The paired comparison tool includes `end_to_end_exact_story`; regenerate older reports with the current scorer before comparisons. Legacy reports without this field are not equivalent.

## Real-model acceptance is still separate

- The frozen Chinese dataset has only **12** stories; a strong score on it is **not** evidence of cross-industry generalization or statistical significance.
- Unit tests use gold-derived oracle fixtures and intentionally wrong-value counterexamples to verify **the scorer**. They are **not** a live LLM benchmark result.
- `scripts/eval/capture_zh_frozen_http.py` captures actual configured extractor candidates, HTTP memory persistence, HTTP search and local answer model calls. This four-stage harness does **not** currently exercise the separate `ground_with_memory: true` API's server-injected canonical evidence. It cannot stand alone as proof that the end-to-end evidence-grounded revision path is reliable.
- To qualify the actual evidence-grounded API, run independent tests with a real configured model and authenticated disposable tenant, capture canonical evidence IDs, guard outcomes, revisions, outbox state, retrieval and answers; verify stale references fail closed.
- Never publish scores without the exact dataset hash, run commit, extractor and answer model identifiers, prompt/config, retrieval-channel attestation and complete raw prediction artifact.

## Targeted regression

```bash
python -m pytest -q tests/test_zh_frozen_evaluator.py tests/test_zh_frozen_diagnostics.py
python scripts/eval/score_zh_frozen.py --manifest
```

This verifies scoring semantics only. It does not require a model server and does not claim real inference occurred.
