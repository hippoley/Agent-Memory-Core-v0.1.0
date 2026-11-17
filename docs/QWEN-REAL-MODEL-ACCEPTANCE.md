# Qwen real-model P0 acceptance (manual and billable)

This workflow executes the **real DashScope/Qwen API** against an ephemeral GitHub Actions PostgreSQL database and loopback Memory API. It **does not** substitute a stub extractor. It is disabled for push and pull-request events to avoid unintentional billing or exposing secrets.

## One-time operator setup

1. In the repository Actions secrets, add `DASHSCOPE_API_KEY` from a purpose-limited Alibaba Cloud Model Studio account. Do not commit the key or paste it in issues.
2. Open **Actions → qwen-real-memory-acceptance → Run workflow**, select `main`, choose a Qwen model ID supported by your DashScope region (default repository preset `qwen-plus`).
3. GitHub Actions creates a fresh ephemeral PostgreSQL + AGE + pgvector service, migrates the database, starts an authenticated local Memory API, and runs the *Chinese* grounded correction probe. A real cloud request occurs during processing. The workflow exports a `qwen-real-memory-evidence` artifact even if the probe fails.
4. Download `evidence/qwen-grounded-atomic.json` and `evidence/manifest.json`. Verify `manifest.report_sha256`, `github_sha`, and the run URL. Do not accept an absent artifact as a pass.

The workflow uses `MEMORY_EXTRACTOR_PROVIDER=dashscope` and
`MEMORY_EXTRACTOR_REQUIRE_MODEL_ATTESTATION=1`. It does not use an Ollama service,
a pre-written model response, or a shared database. It tests one Chinese
supplier settlement correction (`monthly → quarterly`) and requires:
actual pre-revision search retrieval of the old canonical Memory ID; a real model
response matching the requested model ID; request/response hashes; atomic
old-ID retirement and a new active ID; matching follow-up search; and a pass
from the strict live probe.

The report is **not** a statistically meaningful multilingual or cross-industry
quality score. It is also **not** a restart or post-restart proof, independent
signed receipt, or proof that the Qwen endpoint actually served the advertised
weight bytes. Those are separate P0 gates.

### Security and privacy

The real provider sees the user message and the evidence-grounding content sent
by the extractor. This workflow only uses a synthetic supplier scenario.
Never point it to production memories or real user data. It requires a
repository secret `DASHSCOPE_API_KEY`, does not print it, uses a random isolated
API credential/tenant, binds API and database to runner loopback, and deletes
the disposable database at the end. Artifacts only contain redacted attestation
metadata and synthetic test identifiers (retained 14 days).

### Failure interpretation

- Missing secret/model configuration: setup fails; this is **not** a model failure.
- `failure_stage: grounding_retrieval`: the old claim was not retrievable
  through the actual correction query, so inference is not attempted.
- `failure_stage: exception`: an API/model/runtime failure, not a quality pass.
- `passed: false` with complete checks: compare each canonical/retrieval/model gate.
- `passed: true`: the **single** real-model synthetic E2E scenario passed.
  Still not a P0 certification.

For full P0 qualification, continue with frozen multi-turn cases, cross-session
readback, separate process restart and independent receipt verifier.

### Local reproduction

Configure a disposable PostgreSQL-backed API with a DashScope extractor and use:

```bash
python scripts/eval/probe_grounded_atomic_live.py \
  --base-url http://127.0.0.1:8767 \
  --extractor-model qwen-plus \
  --provider openai-compatible \
  --language zh \
  --ack-disposable \
  --report evidence/qwen-grounded-atomic.json
```

The API bearer credential is read from `MEMORY_API_KEY`. The external Qwen
API credential must be configured **on the server** via
`MEMORY_EXTRACTOR_API_KEY` (never sent to the probe client).
