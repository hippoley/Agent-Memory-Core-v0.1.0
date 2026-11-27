# AMC · 8.0 engineering acceptance candidate (PR #65)

This package **does not demonstrate** an 8.0/10 product outcome by itself. It provides a two-stage, operator-run, fail-closed path toward that outcome. The exact model weights, AMC server, physical device and independent gateway **have not been accessed from this workspace**. Do **not** mark the acceptance gate as passed without the generated live reports and independent device logs.

## Phase 1 — prove local Qwen tool call without device or AMC writes

On the AMD host running the local Qwen OpenAI-compatible `/v1/chat/completions` server, first check it advertises the pinned model in `/v1/models`:

```bash
# Check out the PR #65 branch with handoff_model_agent.py available.
python -m pip install -e '.[api,postgres]'
python -m pip install httpx
export QWEN_CHAT_COMPLETIONS_URL='http://127.0.0.1:8000/v1/chat/completions'
export QWEN_MODEL_ID='<exact model ID from http://127.0.0.1:8000/v1/models>'
python probe_local_qwen.py --device master-bedroom --property temperature --target 26 --output out/qwen-preflight.json
```

**Success gate:** the *live endpoint* returns a structured `set_device_property` call, with exact expected device/property/value and pinned model identity. The preflight reads `/v1/models` and generates an independent decision request via the same `choose_device_action` used by Agent B; it does not issue AMC writes or device commands. The preflight claim is deliberately synthetic and is **not** represented as a real AMC Handoff. `live_endpoint_response_observed` does **not** attest the underlying weights or prove that the service isn't a stub. Retain inference-server logs and model checksum for external audit. Any error or mismatch exits nonzero.

## Phase 2 — exercise AMC handoff and independent device readback

Only use a disposable/authorized device and a safe command range. A real command is not idempotently replayable from this script; **never blindly retry an UNKNOWN outcome**. Prepare two separate gateway deployments, one write-only and one read-only, using distinct origins and credentials. Merely providing separate domains and tokens is not proof of physical independence.

```bash
export MEMORY_API_URL='http://127.0.0.1:8765'
export MEMORY_API_KEY='<AMC API key>'
export MEMORY_TENANT_ID='<isolated test tenant>'
export DEVICE_COMMAND_URL='https://command-gateway.example/command'
export DEVICE_COMMAND_TOKEN='<command scoped token>'
export DEVICE_OBSERVER_URL='https://observer-gateway.example/observe'
export DEVICE_OBSERVER_TOKEN='<observer scoped token>'
# QWEN_CHAT_COMPLETIONS_URL and QWEN_MODEL_ID from Phase 1 stay set.
python run_live_handoff.py --device master-bedroom --property temperature --target 26 \
  --output out/handoff-effect.json --safe-min 20 --safe-max 30 \
  --confirm-live-device-write
```

Phase 2 **also performs Phase 1 again before any AMC or physical side effect**, and aborts if the pinned model cannot produce the required tool call. It then exercises two separate AMC HTTP clients, handoff acceptance, model-generated tool selection, an HTTPS actuator, an HTTPS independent observer, and a persisted receipt reread. In the supplied code, the command endpoint expects `POST {"device_id":"...","property":"...","value":26}` and the observer expects `GET ?device_id=...&property=...` returning `{"device_id":"...","temperature":26,"source":"hardware"}`. The gateway operators must implement and audit those semantics. The script never uses command ACK as success evidence.

**Failure means no success receipt**, but a failed command or readback may leave the physical device in an unknown state. No automatic replay. To claim an independently corroborated run, include command and observer gateway event IDs, timestamps, model server build and weights hash, AMC tenant and artifact IDs, and a third-party audit of the readback's independence. The local JSON report alone does not prove these.

## Evidence-based scoring (not automatic scores)

- **7.3 provisional:** engineering contract, 6/6 CI, mocked local model/observer tests.
- **8.0 candidate gate:** on a machine you control, at least one *actual* pinned Qwen inference, a new AMC tenant with separate Agent A/B clients, a real approved device operation and independently audited readback, persisted receipt verified by an additional reader, and all evidence attached with failures disclosed. This is a **minimum product demonstration**, not a statistical comparison.
- **8.5 gate:** repeatable, preregistered matched no-memory / plain summary / AMC experiment; ≥30 nontrivial Chinese future tasks per arm, target ≥85% AMC true completion, ≥10 percentage points over plain summary with confidence interval, no ground-truth leakage, and verifiable source/effect receipts.

## Local regression (does not execute hardware)

```bash
python -m pytest -q test_live_handoff_runner.py test_probe_local_qwen.py
```

The test cases use protocol stubs intentionally; the only way to generate actual model evidence is to run Phase 1 against a real pinned Qwen service. Never submit synthetic reports as live model or physical-device evidence.

## v3 evidence hardening — challenge-response, not benchmark proof

The read-only Phase 1 now issues **three distinct challenges by default**: the
requested target and two cryptographically selected nearby targets. Every call
must pass the same production `choose_device_action` contract. This catches a
service that always returns one static setpoint, but **does not** authenticate
model weights, prevent a sufficiently adaptive fake service, or prove tool use
in the physical environment. Temperature variations are for simulated *model
requests only*: **no devices are touched in Phase 1**. Use `--challenges 3`
(default; accepted range 2–8) when collecting a baseline. Do not use a
production device as a target without an authorized test plan.

For each request the report includes SHA-256 digests of the serialized HTTP
request and raw HTTP response, response status and elapsed time. Digests make
accidental result substitution more detectable; **they are not cryptographic
signatures or independent source attestation**. Raw model outputs and API
credentials are not included in the report. Retain private, independently
protected inference-server logs if model provenance is part of your claim.

Phase 2 independently repeats the three model challenges **before** creating
an AMC task or writing any device. It records the challenge targets and
request/response digests alongside the effect receipt. It explicitly marks
`model_weights_independently_verified=false` and
`observer_physical_independence_verified=false` until an external verifier
supplies stronger evidence. Distinct observer URL/token, and a JSON
`source=hardware` label, **cannot independently prove hardware readback**.

To claim 8.0 acceptance, supply the exact model weights checksum + runtime
logs, AMC tenant/task/handoff identifiers, an authorized command event ID and
independent observer's signed audit logs, along with the readback receipt.
Without these, the output is an operator-run smoke test, not an independently
reproduced product result. Do not blindly retry an unknown physical effect.


## v4 · Device-effect safety and UNKNOWN reconciliation

**Mandatory operator limits:** Phase 2 requires `--safe-min` and `--safe-max`
representing the authorized setpoint range for *that specific device and
property*. Values outside the range are rejected **before any AMC or device
request**. The sample `20..30` above is only illustrative, not a universally
safe temperature range. Confirm device units, target suitability, and
permissions externally.

**Run-once evidence path:** Phase 2 reserves `--output` using exclusive file
creation **before** creating a task. Reusing that path fails instead of
silently overwriting a prior receipt or blindly retrying a potentially
completed device command. Use a unique output path for each authorized
attempt; do not treat a different path as an idempotency guarantee. For
production retry safety you need a server-side idempotency key and device-
side command reconciliation, which this standalone tool does not provide.

**Failure semantics:** The report now has `actuation_attempted`,
`actuation_acknowledged`, `readback_after_attempt_observed`, `phase`, and a
random `run_id` to distinguish preflight failure from uncertain physical
execution. An exception **after beginning an external command** yields
`status=unknown_effect` and `phase=requires_manual_reconciliation`; it must
not be treated as a safe failure or automatically retried. A successful
command HTTP response is not completion evidence; success still requires
observer readback and the AMC receipt reread.

**Crash boundary:** The script checkpoints stage state before attempting the
HTTP command, but a host crash, filesystem loss, or a malicious gateway can
still compromise operator claims. A reserved/incomplete output is never a
success report. Restore actual device state from independent gateway records
before considering a new run. The generated report is *not* signed or
independent attestation.

**Local stage regression** (pure stubs only; no actual device, model, AMC):

```bash
python -m pytest -q test_live_handoff_runner.py test_probe_local_qwen.py test_run_phases_v4.py
```

The added stage tests cover success, command timeout with UNKNOWN (and
same-path replay prevention), failed model preflight with no actuation, and
out-of-range targets rejected before I/O.

## v5 · UNKNOWN 只读对账（本轮新增）

v4 运行器发生网络超时或设备回读故障时，可能得到 `unknown_effect`。
这**不等于执行失败**，也绝不可自动再次调用设备写入。

v5 新增 `reconcile_unknown.py`：只使用设备观察凭据调用单次 HTTPS GET，
绝不向命令端点 POST。它接受的输入必须是一次已标记为 `actuation_attempted=true`
且处于 UNKNOWN 的 v4 报告，或已开始命令但尚未完成最终报告的中断状态。

```bash
export QWEN_CHAT_COMPLETIONS_URL='http://127.0.0.1:8000/v1/chat/completions'
export DEVICE_COMMAND_URL='https://command.example.com/command'
export DEVICE_OBSERVER_URL='https://observer.example.com/read'
export DEVICE_OBSERVER_TOKEN='YOUR_READ_ONLY_OBSERVER_TOKEN'
python reconcile_unknown.py \
  --report out/handoff-v4-001.json \
  --output out/handoff-v4-001-reconciliation.json
```

对账结果分别是：

- `state_matches_target`：观察端点**现在**报告值等于原目标；不能证明本次命令导致了变化，也不能证明 AMC 收据已经持久化。
- `state_differs_from_target`：观察端点现在报告值与目标不同；不能推断原命令完全没有执行过。
- `readback_unavailable`：读取失败或数据不符合契约；保持不确定，切勿自动重试命令。

对账证据包括原报告 SHA-256、原 run_id、观察时间与响应字节 SHA-256。原报告不作任何修改，输出路径必须是新文件并且不会覆盖已有文件。

**信任边界**：分离 HTTPS 域名和 token，以及 JSON 中的 `source=hardware`，不能证明观察通道在物理上独立。真实设备验收必须另外检查网关部署、设备遥测来源与访问日志。没有 AMD 本地 Qwen 实测或真实设备回读之前，不能给 AMC 提升产品实测分数。

### v5 回归结果

在没有真实设备/模型的沙盒中执行：

```bash
python -m pytest -q test_live_handoff_runner.py test_probe_local_qwen.py test_run_phases_v4.py test_reconcile_unknown.py
```

**26 passed**，包含新增 8 个 UNKNOWN 对账测试。此结果仅证明工具包的模拟边界和只读对账逻辑；不代表实机运行。

## US-M01-LIVE · AMD395 ROCm first-inference startup (non-actuating)

The local Transformers server exposes the same Python `torch.cuda` API on
ROCm/HIP and NVIDIA CUDA. The presence of a `cuda` device string **does not**
by itself establish AMD GPU use. Start with a *verified HIP backend*:

```bash
python - <<'PY'
import torch
print({"torch": torch.__version__, "hip": torch.version.hip,
       "available": torch.cuda.is_available(),
       "name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None})
PY
# Use a model actually installed on the host and supported by the tokenizer.
python scripts/eval/serve_local_llm.py \
  --model Qwen/Qwen2.5-7B-Instruct \
  --host 127.0.0.1 --port 8000 --device cuda --require-rocm

# Run from another shell (no AMC or hardware write):
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/v1/models
python examples/amc_live_acceptance/probe_local_qwen.py \
  --device preflight-bedroom --property temperature --target 26 \
  --output out/amd395-qwen-tool-preflight.json
```

The server refuses startup when `--require-rocm` is set and PyTorch lacks
HIP or cannot access the GPU. Its `/healthz` reports `execution_backend`,
`device_name`, and `torch_hip_version` from the loaded runtime. This is
a diagnostic, **not hardware provenance attestation**. Keep the model revision
and weight hashes, runtime launch log, and independently captured GPU utilization
for a reproducible live result. Even a successful probe is **not** a physical
device effect or a cross-session AMC win. Never use an authorized device endpoint
for this first read-only stage.

