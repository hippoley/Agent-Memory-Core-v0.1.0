# Chinese Memory Extraction Evaluation

This fixture set is a **behavioral evaluation harness**, not a claim that any
model already passes. It covers positive/negative preference, corrections,
multiple facts, grounded and ambiguous pronouns, assistant suggestions, and
temporary-vs-durable statements.

## Run against a local model

```bash
export MEMORY_LOCAL_MODEL_URL='http://127.0.0.1:1234/v1'
export MEMORY_LOCAL_MODEL='your-local-qwen-model-name'
python scripts/evaluate_local_extractor.py
```

The runner reports exact candidate precision/recall/F1 and polarity accuracy.
A polarity mismatch counts as both a false positive and false negative; a model
cannot receive full credit by extracting the right value while reversing
negation. Keep model version, quantization, prompt version and dataset SHA
alongside each result before comparing runs.

The initial 12-case set is a smoke benchmark only. It is not statistically
representative and should be expanded with human-adjudicated examples before
using its scores as release gates.
