## Real HTTP capture runner (new)

The strict scorer can now consume predictions captured by a **real** PostgreSQL-backed HTTP instance, a selected extractor (rule or local model), and an OpenAI-compatible **local answer model**. This is a runnable harness, **not a published accuracy score**. It intentionally refuses non-loopback URLs, missing answer models, and any tenant containing active claims.

Start the existing real stack using a **fresh, disposable tenant**, then start an OpenAI-compatible local model server (e.g. vLLM/Ollama-compatible endpoint). The service must have the desired query-alias configuration already enabled or disabled; \`--aliases\` documents that experiment configuration but cannot change server settings.

\`\`\`bash
python scripts/eval/capture_zh_frozen_http.py \
  --base-url http://127.0.0.1:8767 \
  --key "$MEMORY_API_KEY" \
  --extractor rule \
  --name rule-context-off-aliases-on \
  --aliases \
  --answer-url http://127.0.0.1:8000/v1/chat/completions \
  --answer-model YOUR_LOCAL_MODEL \
  --ack-disposable \
  --predictions /tmp/zh-capture.json \
  --report /tmp/zh-score.json
\`\`\`

To exercise the configured local model extractor instead, set \`MEMORY_EXTRACTOR_PROVIDER\` and model settings (loopback only) and use \`--extractor model\`. Model extractors without \`extract_with_context\` **reject** \`--context\`, rather than quietly ignoring it. All four stages are captured as actual outputs: **raw candidates**, API-updated canonical active claims, real search Top 5 and answer-model text. Each case is separated by soft-forgetting prior active claims on the disposable tenant, leaving its audit history intact.

**Limits:** never run on a tenant with meaningful history; requires fresh tenant and a local answer model. The current alias switch is declarative and must match the instance's effective configuration; automatic attestation of server-side aliases is still outstanding. Separate immutable evidence hashes for model weights, quantization and prompt remain needed for publication. For safety, do not interpret 12-case scores as broad generalization.
