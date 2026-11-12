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

app = FastAPI()
_lock = threading.Lock()
_state: dict = {}


@app.get("/healthz")
def healthz():
    return {"ok": True, "model": _state.get("model_id")}


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

    tokenizer = _state["tokenizer"]
    model = _state["model"]
    prompt = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True)

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

    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:16],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": _state["model_id"],
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": int(inputs["input_ids"].shape[1]),
            "completion_tokens": int(generated.shape[0]),
            "total_tokens": int(inputs["input_ids"].shape[1] + generated.shape[0]),
        },
        "latency_s": round(time.time() - started, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="Qwen/Qwen2.5-7B-Instruct")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--max-model-len", type=int, default=8192)
    args = parser.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    try:  # transformers >= 5 renamed torch_dtype to dtype
        model = AutoModelForCausalLM.from_pretrained(
            args.model, dtype=torch.bfloat16, device_map="cuda")
    except TypeError:
        model = AutoModelForCausalLM.from_pretrained(
            args.model, torch_dtype=torch.bfloat16, device_map="cuda")
    model.eval()
    _state.update(model=model, tokenizer=tokenizer, model_id=args.model)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
