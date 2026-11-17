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

## Authentication failures established by real runs (2026-11-17)

Owner-authorized one-shot Actions exercises reached PostgreSQL and the authenticated
API, then the real Model Studio upstream refused both Beijing and Singapore shared
DashScope endpoints with **HTTP 401**. This proves secret *presence*, not its
validity. No real Qwen memory correction succeeded in those runs. Do not report
the 401 sessions as model evaluation passes.

The manual workflow first performs a synthetic Qwen JSON-mode authentication
smoke **before** starting the database and does not send real user memories.
For a standard, pay-as-you-go Model Studio key, use an `sk-` key, not a
plan-specific `sk-sp-` key (Coding Plan and Token Plan usage is not permitted
for this noninteractive backend workflow). Match the API key region to the
endpoint region; the shared Beijing domain is the default.

The operator can set the optional GitHub Actions **repository variable**
`DASHSCOPE_BASE_URL` to the full OpenAI-compatible chat/completions endpoint
for the **same region and billing plan** as the API key. For example, for a
Singapore pay-as-you-go API key:
`https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions`.
Use normal GitHub Actions Secrets for the key itself. Do not place the key in
a variable, command, or repository file.

If the provider smoke returns HTTP 401, regenerate or correct the pay-as-you-go
API key in the matching region; do not change the test to accept synthetic
responses. Once the smoke returns HTTP 200 with a matching model, the rest
of the gated PostgreSQL + API + canonical-history probe runs normally.

## Post-restart independent acceptance (P0.4, opt-in)

The real Qwen workflow now adds a **separate process-restart acceptance gate**
after its successful atomic correction. It kills the existing Uvicorn process,
checks the prior API is no longer healthy, starts a new Uvicorn PID against the
*same disposable PostgreSQL volume*, and runs
`scripts/eval/verify_qwen_postrestart.py` from a new Python process and a
fresh HTTP client/session. The original Qwen extractor is **not** called again.

`evidence/qwen-postrestart-readback.json` must show all of the following:
fresh API readiness, exactly one active quarterly value with the exact new ID,
retained historical old and new records, new ID retrievable by search, and old
ID absent from active search. The manifest records a SHA-256 digest of both
primary and post-restart reports and sets its final `passed` flag only when
**both** reports pass. If restart fails, the primary receipt may still pass, but
P0.4 is not passed.

This is a **new-API-process, new-client-session** persistence proof, not a
PostgreSQL-container power-cycle proof, multitenant authentication exercise,
nor full five-semantics benchmark. Those remain separate gates.
