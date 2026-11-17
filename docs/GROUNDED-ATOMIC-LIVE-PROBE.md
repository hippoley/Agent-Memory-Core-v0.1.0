# Evidence-grounded atomic revision · real-model probe

The existing `ground_with_memory` path retrieves the authenticated tenant's canonical evidence, passes it to an evidence-capable extractor, guards claimed retraction IDs/values, and applies model proposals. The PostgreSQL acceptance test now runs both the legacy partial-accept mode and the strict `atomic_batch: true` transaction mode, using an **instrumented stub model**. This validates wiring and database behavior only.

To qualify **real inference**, run the opt-in script `scripts/eval/probe_grounded_atomic_live.py` against a freshly deployed **isolated single-tenant**, PostgreSQL-backed loopback API with a **real evidence-capable LLM extractor**, not a stub.

```bash
python scripts/eval/probe_grounded_atomic_live.py \
  --base-url http://127.0.0.1:8767 \
  --key "$MEMORY_API_KEY" \
  --extractor-model "actual-local-model-id" \
  --ack-disposable \
  --report /tmp/grounded-atomic-live.json
```

The script refuses a non-loopback endpoint, credentials inside URL, or any tenant with prior canonical history. It creates one new random supplier subject, writes its monthly settlement fact, asks the **server-configured** LLM to ground a correction to quarterly, requests **strict atomic** processing, and independently reads historical and current claims plus search. It exits nonzero if the old memory remains active, quarterly fact is missing/duplicated, replacement ID is absent, or retrieval fails. Retrieval must point to the exact new canonical `memory_id`, not merely a matching subject/value, and must not return the retired memory ID.

**Limitations:**
- The script does not start a model, provision a tenant, or prove the server really loaded the *declared* `--extractor-model` string. Capture server configuration/model request logs separately; the CLI field is operator-declared metadata only.
- An actual successful invocation must retain the raw report, model server version/weights/quantization, prompts, API and DB versions, SHA, model access logs and independent signed results before being called a real LLM acceptance.
- The script intentionally does **not** clean up records automatically. Run only on a disposable empty tenant/database. HTTP or inference errors must be treated as failures, never substituted with fixture results.
- This is **one synthetic supplier example**, not a cross-industry benchmark, multi-session correctness certification, production recovery proof, or comprehensive security audit.
- On error before final report writing, the command exits nonzero and the original exception is preserved; no misleading successful report is produced.

Current gate status: **code and opt-in probe committed; live model execution not yet evidenced.**
