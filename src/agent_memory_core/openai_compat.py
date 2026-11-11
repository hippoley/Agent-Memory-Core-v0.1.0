"""OpenAI-compatible chat adapter for memory fact extraction.

One adapter covers every provider that exposes an OpenAI-style
``POST {base_url}/chat/completions`` endpoint with optional Bearer auth:
hosted APIs (DeepSeek, DashScope/Qwen, Zhipu, OpenAI, ...) and self-hosted
runtimes (vLLM, SGLang, LM Studio, or Ollama's OpenAI-compatible endpoint).

The adapter only formats a strict fact-extraction request and validates the
response against the shared fact contract. It performs no storage, no
fan-out and no other network calls; memory data leaves the process only
toward the configured endpoint the operator chose.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .fact_contract import EXTRACTOR_INSTRUCTION, untrusted_user_payload, validate_fact_candidates


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Extractor endpoint redirect refused")


class OpenAICompatFactExtractor:
    def __init__(self, *, model: str, base_url: str, api_key: str | None = None,
                 timeout: float = 30.0, json_mode: bool = True):
        if not isinstance(model, str) or not model.strip() or timeout <= 0:
            raise ValueError("A model and positive timeout are required")
        parsed = urlsplit(base_url)
        if (parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username or parsed.password
                or parsed.query or parsed.fragment
                or not parsed.path.rstrip("/").endswith("/chat/completions")):
            raise ValueError(
                "base_url must be an http(s) URL whose path ends with /chat/completions")
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.json_mode = json_mode
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect())

    def __call__(self, text: str) -> list[dict]:
        if not isinstance(text, str) or not text.strip() or len(text) > 100000:
            raise ValueError("Nonempty text up to 100000 characters required")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": EXTRACTOR_INSTRUCTION},
                {"role": "user", "content": untrusted_user_payload(text)},
            ],
            "temperature": 0,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.base_url, data=body,
                                         headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(1024 * 1024 + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Extractor endpoint unavailable") from exc
        if len(raw) > 1024 * 1024:
            raise RuntimeError("Extractor response exceeds 1 MiB")
        try:
            outer = json.loads(raw)
            content = outer["choices"][0]["message"]["content"]
            facts = json.loads(content)["facts"]
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise RuntimeError("Extractor returned invalid JSON contract") from exc
        return validate_fact_candidates(facts)
