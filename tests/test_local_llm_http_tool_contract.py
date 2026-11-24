"""Exercise the HTTP Qwen tool-call contract with fake weights, never a fake tool receipt."""
import asyncio
import importlib.util
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import httpx


class Tokens:
    def __init__(self, length):
        self.shape = (length,)

    def __getitem__(self, _):
        return self


class Tokenized(dict):
    def __init__(self):
        super().__init__(input_ids=Tokens(3))

    def to(self, _):
        return self


class Tokenizer:
    eos_token_id = 1

    def __init__(self):
        self.tools_seen = None

    def apply_chat_template(self, messages, **kwargs):
        self.tools_seen = kwargs.get("tools")
        return "mock-prompt"

    def __call__(self, prompt, **kwargs):
        return Tokenized()

    def decode(self, generated, **kwargs):
        return '<tool_call>{"name":"set_device_property","arguments":{"device_id":"room","property":"temperature","value":26}}</tool_call>'


class Model:
    device = "cpu"

    def generate(self, **kwargs):
        return [Tokens(4)]


def test_model_http_tool_contract(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(inference_mode=nullcontext))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        AutoModelForCausalLM=object, AutoTokenizer=object))
    path = Path(__file__).resolve().parents[1] / "scripts/eval/serve_local_llm.py"
    spec = importlib.util.spec_from_file_location("amc_local_llm_contract_server", path)
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    tokenizer = Tokenizer()
    server._state.update(model=Model(), tokenizer=tokenizer, model_id="local-qwen")
    tools = [{"type": "function", "function": {
        "name": "set_device_property", "parameters": {"type": "object"}}}]

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                     base_url="http://local") as client:
            good = await client.post("/v1/chat/completions", json={
                "model": "local-qwen", "messages": [{"role": "user", "content": "Set temp"}],
                "tools": tools, "tool_choice": "required"})
            assert good.status_code == 200, good.text
            result = good.json()
            assert result["model"] == "local-qwen"
            assert result["choices"][0]["finish_reason"] == "tool_calls"
            call = result["choices"][0]["message"]["tool_calls"][0]
            assert call["function"]["name"] == "set_device_property"
            assert json.loads(call["function"]["arguments"])["value"] == 26
            assert tokenizer.tools_seen == tools
            wrong_model = await client.post("/v1/chat/completions", json={
                "model": "different-model",
                "messages": [{"role": "user", "content": "Set"}]})
            assert wrong_model.status_code == 400
    asyncio.run(check())
