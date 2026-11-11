"""Ollama adapter for local-only memory fact extraction.

Operator must install and run Ollama locally. No hosted LLM, no outbound URL,
no model downloads by this adapter. Model quality requires a separate benchmark.
The prompt format and fact validation are shared with the OpenAI-compatible
adapter via :mod:`agent_memory_core.fact_contract`.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .fact_contract import EXTRACTOR_INSTRUCTION, untrusted_user_payload, validate_fact_candidates


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Ollama redirect refused; no remote network access")


class OllamaFactExtractor:
    def __init__(self, *, model: str, endpoint: str = "http://127.0.0.1:11434/api/generate",
                 timeout: float = 20.0):
        parsed=urlsplit(endpoint)
        if (parsed.scheme != "http"
                or parsed.hostname not in ("127.0.0.1","localhost","::1")
                or parsed.path != "/api/generate"
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Only a loopback Ollama /api/generate URL is allowed")
        if not isinstance(model,str) or not model.strip() or timeout <= 0:
            raise ValueError("A local model and positive timeout are required")
        self.model=model
        self.endpoint=endpoint
        self.timeout=timeout
        self._opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())

    def __call__(self, text: str) -> list[dict]:
        if not isinstance(text,str) or not text.strip() or len(text)>100000:
            raise ValueError("Nonempty text up to 100000 characters required")
        payload=json.dumps({"model":self.model,"prompt":EXTRACTOR_INSTRUCTION+untrusted_user_payload(text),
            "stream":False,"format":"json",
            "options":{"temperature":0}},ensure_ascii=False).encode("utf-8")
        request=urllib.request.Request(self.endpoint,data=payload,
                 headers={"Content-Type":"application/json"},method="POST")
        try:
            with self._opener.open(request,timeout=self.timeout) as response:
                raw=response.read(1024*1024+1)
        except (urllib.error.URLError,TimeoutError,OSError) as exc:
            raise RuntimeError("Local Ollama memory extractor unavailable") from exc
        if len(raw)>1024*1024:
            raise RuntimeError("Local extractor response exceeds 1 MiB")
        try:
            outer=json.loads(raw)
            inner=json.loads(outer["response"])
            facts=inner["facts"]
        except (ValueError,TypeError,KeyError) as exc:
            raise RuntimeError("Local extractor returned invalid JSON contract") from exc
        return validate_fact_candidates(facts)
