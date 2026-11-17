# Evidence-grounded atomic revision · real-model probe

The existing `ground_with_memory` path retrieves the authenticated tenant's canonical evidence, passes it to an evidence-capable extractor, guards claimed retraction IDs/values, and applies model proposals. The PostgreSQL acceptance test now runs both the legacy partial-accept mode and the strict `atomic_batch: true` transaction mode, using an **instrumented stub model**. This validates wiring and database behavior only.

To qualify **real inference**, run the opt-in script `scripts/eval/probe_grounded_atomic_live.py` against a freshly deployed **isolated single-tenant**, PostgreSQL-backed loopback API with a **real evidence-capable LLM extractor**, not a stub.

```bash
python scripts/eval/probe_grounded_atomic_live.py \
  --base-url http://127.0.0.1:8767 \
  --key "$MEMORY_API_KEY" \
  --extractor-model "actual-local-model-id" \
  --provider ollama \
  --ack-disposable \
  --report /tmp/grounded-atomic-live.json
```

Set `MEMORY_EXTRACTOR_REQUIRE_MODEL_ATTESTATION=1` on the API process. The script refuses a non-loopback endpoint, credentials inside URL, or any tenant with prior canonical history. The authenticated process response includes redacted `model_call_attestation` metadata: requested/response model, request and response hashes, response ID, system fingerprint and token usage for OpenAI-compatible providers; Ollama additionally records `/api/version`, `/api/tags` digest, family, parameter size and quantization metadata. The script requires response-model identity to match the configured model; for Ollama it additionally requires a model digest and runtime version. It creates one new random supplier subject, writes its monthly settlement fact, asks the **server-configured** LLM to ground a correction to quarterly, requests **strict atomic** processing, and independently reads historical and current claims plus search. It exits nonzero if the old memory remains active, quarterly fact is missing/duplicated, replacement ID is absent, or retrieval fails. Retrieval must point to the exact new canonical `memory_id`, not merely a matching subject/value, and must not return the retired memory ID.

**Limitations:**
- OpenAI-compatible response model and system fingerprint are server-supplied metadata, not cryptographic proof of weights. Ollama digest and quantization are read from the local model registry and are stronger reproducibility evidence, but are still reported by the runtime itself, not independently signed hardware attestation.
- Request/response hashes are captured without retaining prompts, credentials or raw response text in the API result. Preserve secured runtime logs and model files separately if the experiment requires full replay.
- An actual successful invocation must retain the raw report, model server version/weights/quantization, prompts, API and DB versions, SHA, model access logs and independent signed results before being called a real LLM acceptance.
- The script intentionally does **not** clean up records automatically. Run only on a disposable empty tenant/database. HTTP or inference errors must be treated as failures, never substituted with fixture results.
- This is **one synthetic supplier example**, not a cross-industry benchmark, multi-session correctness certification, production recovery proof, or comprehensive security audit.
- On error before final report writing, the command exits nonzero and the original exception is preserved; no misleading successful report is produced.

Current gate status: **code and opt-in probe committed; live model execution not yet evidenced.**
