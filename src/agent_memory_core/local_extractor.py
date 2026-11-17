"""Ollama adapter for local-only memory fact extraction.

Operator must install and run Ollama locally. No hosted LLM, no outbound URL,
no model downloads by this adapter. Model quality requires a separate benchmark.
The prompt format and fact validation are shared with the OpenAI-compatible
adapter via :mod:`agent_memory_core.fact_contract`.
"""
from __future__ import annotations

import json
import hashlib
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .fact_contract import EXTRACTOR_INSTRUCTION, untrusted_user_payload, validate_fact_candidates


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Ollama redirect refused; no remote network access")


class OllamaFactExtractor:
    def __init__(self, *, model: str, endpoint: str = "http://127.0.0.1:11434/api/generate",
                 timeout: float = 20.0, require_model_attestation: bool = False):
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
        self.require_model_attestation=require_model_attestation
        self.attestations: list[dict] = []
        self._opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())

    def __call__(self, text: str) -> list[dict]:
        return self.extract_with_context(text, [])

    def extract_with_evidence(self, text: str, context_turns: list[str],
                              memory_evidence: list[dict]) -> list[dict]:
        """Propose facts against tenant-scoped canonical evidence.

        The caller must validate retraction proposals against evidence and
        PostgreSQL must recheck the current value at execution.
        """
        return self._extract(text, context_turns, memory_evidence)

    def extract_with_context(self, text: str, context_turns: list[str]) -> list[dict]:
        return self._extract(text, context_turns, None)

    def _extract(self, text: str, context_turns: list[str],
                 memory_evidence: list[dict] | None) -> list[dict]:
        """Allow any domain's bounded prior dialogue as untrusted model input."""
        if not isinstance(text,str) or not text.strip() or len(text)>100000:
            raise ValueError("Nonempty text up to 100000 characters required")
        payload=json.dumps({"model":self.model,"prompt":EXTRACTOR_INSTRUCTION+untrusted_user_payload(text,context_turns=context_turns,memory_evidence=memory_evidence),
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
        runtime = self._runtime_metadata()
        response_model = outer.get("model")
        tags = runtime.get("model_tag") or {}
        model_match = response_model == self.model
        digest = tags.get("digest")
        self.attestations.append({
            "provider_protocol": "ollama-native-api-generate",
            "requested_model": self.model, "response_model": response_model,
            "identity_status": "matched" if model_match and digest else (
                "mismatch" if response_model and not model_match else "unattested"),
            "model_digest": digest,
            "model_details": tags.get("details") if isinstance(tags.get("details"), dict) else None,
            "runtime_version": runtime.get("version"),
            "created_at": outer.get("created_at"), "done_reason": outer.get("done_reason"),
            "total_duration_ns": outer.get("total_duration"),
            "load_duration_ns": outer.get("load_duration"),
            "prompt_eval_count": outer.get("prompt_eval_count"),
            "eval_count": outer.get("eval_count"),
            "request_sha256": hashlib.sha256(payload).hexdigest(),
            "response_sha256": hashlib.sha256(raw).hexdigest(),
        })
        if self.require_model_attestation and not (model_match and digest and runtime.get("version")):
            raise RuntimeError("Ollama model identity could not be independently attested")
        return validate_fact_candidates(facts)

    def _runtime_metadata(self) -> dict:
        """Read Ollama local registry/version; missing evidence remains explicit."""
        parsed = urlsplit(self.endpoint)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        result = {"version": None, "model_tag": None}
        for suffix, key in (("/api/version", "version"), ("/api/tags", "tags")):
            request = urllib.request.Request(origin + suffix, method="GET")
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    continue
                data = json.loads(raw)
                if key == "version":
                    result["version"] = data.get("version")
                else:
                    models = data.get("models", [])
                    result["model_tag"] = next(
                        (item for item in models
                         if item.get("name") == self.model or item.get("model") == self.model),
                        None)
            except Exception:
                continue
        return result

    def attestation_snapshot(self) -> dict:
        """Return redacted runtime/model evidence, never prompts or credentials."""
        return {
            "provider": "ollama", "configured_model": self.model,
            "require_model_attestation": self.require_model_attestation,
            "calls": list(self.attestations),
            "identity_verified": bool(self.attestations) and all(
                call["identity_status"] == "matched" for call in self.attestations),
            "cryptographic_weight_identity": False,
        }
