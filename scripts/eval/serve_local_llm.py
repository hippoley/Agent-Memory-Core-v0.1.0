"""Minimal OpenAI-compatible chat server for a local causal LM on one GPU.

The evaluation harness needs a *real* model behind the extractor, but the
cluster node that hosts the memory service cannot reach hosted provider APIs.
This process exposes the exact surface the memory core's OpenAI-compatible
adapter expects — ``POST /v1/chat/completions`` with ``choices[0].message.content``
— so the shipped adapter is exercised unchanged, with no test-only code path.

Deliberately small: one model, one request at a time (a lock), greedy decoding.
That is enough for deterministic fact extraction and answer generation, and it
keeps the evaluation reproducible.

Usage:
  python scripts/eval/serve_local_llm.py --model Qwen/Qwen2.5-7B-Instruct --port 8000
"""
from __future__ import annotations

import argparse
import json
import threading
import time
import uuid

import torch
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from transformers import AutoModelForCausalLM, AutoTokenizer
from agent_memory_core.local_tool_calls import NativeToolCallError, decode_qwen_tool_call

app = FastAPI()
_lock = threading.Lock()
_state: dict = {}


@app.get("/healthz")
def healthz():
    return {"ok": True, "model": _state.get("model_id"),
            "execution_backend": _state.get("execution_backend", "unknown"),
            "device_name": _state.get("device_name", "unknown"),
            "torch_hip_version": _state.get("torch_hip_version") }


@app.get("/v1/models")
def models():
    return {"object": "list",
            "data": [{"id": _state.get("model_id"), "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages") or []
    max_tokens = int(body.get("max_tokens") or 512)
    temperature = float(body.get("temperature") or 0.0)
    if not messages:
        return JSONResponse({"error": {"message": "messages required"}}, status_code=400)
    if body.get("model") not in (None, _state.get("model_id")):
        return JSONResponse({"error": {"message": "Requested model is not loaded"}}, status_code=400)

    tools = body.get("tools")
    if tools is not None and (
            not isinstance(tools, list) or not tools
            or body.get("tool_choice") not in ("required", "auto")):
        return JSONResponse({"error": {"message": "Unsupported tool request"}}, status_code=422)

    tokenizer = _state["tokenizer"]
    model = _state["model"]
    # Qwen's native chat template must receive the tool schemas; omitting
    # them generates only message.content, never real tool_calls.
    try:
        prompt = tokenizer.apply_chat_template(
            messages, tools=tools, tokenize=False, add_generation_prompt=True
        ) if tools is not None else tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True)
    except (TypeError, ValueError, KeyError) as exc:
        if tools is not None:
            return JSONResponse({"error": {"message": "Model tokenizer has no native tool template"}}, status_code=422)
        raise

    started = time.time()
    with _lock:
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        with torch.inference_mode():
            output = model.generate(
                **inputs, max_new_tokens=max_tokens,
                do_sample=temperature > 0,
                temperature=temperature if temperature > 0 else None,
                top_p=1.0, top_k=-1, repetition_penalty=1.02,
                pad_token_id=tokenizer.eos_token_id)
        generated = output[0][inputs["input_ids"].shape[1]:]
        text = tokenizer.decode(generated, skip_special_tokens=True)

    message = {"role": "assistant", "content": text}
    finish_reason = "stop"
    if tools is not None:
        try:
            tool_call = decode_qwen_tool_call(text, tools)
        except NativeToolCallError:
            # Never turn plain model text into a synthetic successful tool
            # call. The client must fail closed, without touching any device.
            return JSONResponse({"error": {"message": "Model did not produce one valid native tool call"}}, status_code=422)
        message = {"role": "assistant", "content": None, "tool_calls": [tool_call]}
        finish_reason = "tool_calls"

    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:16],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": _state["model_id"],
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": int(inputs["input_ids"].shape[1]),
            "completion_tokens": int(generated.shape[0]),
            "total_tokens": int(inputs["input_ids"].shape[1] + generated.shape[0]),
        },
        "latency_s": round(time.time() - started, 2),
    }


def select_execution_backend(torch_module, *, requested: str = "auto", require_rocm: bool = False) -> dict:
    """Fail closed when AMD ROCm was requested but cannot be verified.

    PyTorch HIP intentionally exposes the CUDA API surface; cuda availability
    alone does not prove that the backend is ROCm.
    """
    if requested not in ("auto", "cuda", "cpu"):
        raise ValueError("device must be auto, cuda or cpu")
    hip = getattr(getattr(torch_module, "version", None), "hip", None)
    available = bool(torch_module.cuda.is_available())
    if require_rocm and (not hip or not available or requested == "cpu"):
        raise RuntimeError("ROCm required: install HIP-enabled PyTorch and verify GPU access")
    if requested == "cuda" and not available:
        raise RuntimeError("CUDA/HIP device explicitly requested but torch.cuda.is_available() is false")
    device = "cpu" if requested == "cpu" or (requested == "auto" and not available) else "cuda"
    if device == "cuda":
        name = torch_module.cuda.get_device_name(0)
        backend = "rocm" if hip else "cuda"
    else:
        name = "CPU"
        backend = "cpu"
    return {"device_map": device, "execution_backend": backend,
            "device_name": name, "torch_hip_version": hip}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="Qwen/Qwen2.5-7B-Instruct")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--max-model-len", type=int, default=8192)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto",
                        help="auto selects available GPU (HIP uses CUDA API) or CPU")
    parser.add_argument("--require-rocm", action="store_true",
                        help="fail rather than use CPU or a non-ROCm GPU")
    args = parser.parse_args()
    runtime = select_execution_backend(
        torch, requested=args.device, require_rocm=args.require_rocm)
    print(json.dumps({"event": "amc_model_backend_selected", **runtime}), flush=True)

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    try:  # transformers >= 5 renamed torch_dtype to dtype
        model = AutoModelForCausalLM.from_pretrained(
            args.model, dtype=torch.bfloat16, device_map=runtime["device_map"])
    except TypeError:
        model = AutoModelForCausalLM.from_pretrained(
            args.model, torch_dtype=torch.bfloat16, device_map=runtime["device_map"])
    model.eval()
    _state.update(model=model, tokenizer=tokenizer, model_id=args.model,
                  execution_backend=runtime["execution_backend"],
                  device_name=runtime["device_name"],
                  torch_hip_version=runtime["torch_hip_version"])
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
