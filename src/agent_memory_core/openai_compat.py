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
import math
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .fact_contract import EXTRACTOR_INSTRUCTION, untrusted_user_payload, validate_fact_candidates


def _load_facts(content: str):
    """Read the facts array, tolerating a code fence or a sentence of preamble."""
    try:
        return json.loads(content)["facts"]
    except (ValueError, TypeError, KeyError):
        pass
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        raise ValueError("no JSON object in extractor reply")
    return json.loads(match.group(0))["facts"]


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
        messages = [
            {"role": "system", "content": EXTRACTOR_INSTRUCTION},
            {"role": "user", "content": untrusted_user_payload(text)},
        ]
        # One corrective retry. Small models drift two ways: prose or a code
        # fence around the JSON, and a bare string where an object is required.
        # Re-asking with the exact violation costs one extra call only on
        # malformed output; validation itself is never loosened.
        failure = "invalid JSON contract"
        for attempt in range(2):
            content = self._post(messages)
            try:
                facts = _load_facts(content)
            except (ValueError, TypeError, KeyError, IndexError):
                failure = "invalid JSON contract"
                problem = "the reply was not a JSON object with a 'facts' array"
            else:
                try:
                    return validate_fact_candidates(facts)
                except RuntimeError as exc:
                    failure = "schema"
                    problem = str(exc)
            if attempt:
                break
            messages = messages + [
                {"role": "assistant", "content": content[:4000]},
                {"role": "user", "content":
                    f"That reply was rejected ({problem}). "
                    "Return JSON only, with no prose and no code fence, exactly "
                    "{\"facts\": [...]}. Every fact needs subject_name or "
                    "subject_id, a lowercase snake_case predicate, and value as "
                    "a JSON object, not a string."},
            ]
        if failure == "schema":
            raise RuntimeError("Extractor returned candidate outside the memory fact schema")
        raise RuntimeError("Extractor returned invalid JSON contract")

    def _post(self, messages: list[dict]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
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
            return outer["choices"][0]["message"]["content"]
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise RuntimeError("Extractor returned invalid JSON contract") from exc


class OpenAICompatEmbedder:
    """Batch embedding client for OpenAI-compatible ``/embeddings`` endpoints.

    Any provider exposing ``POST {base_url}`` with ``{"model", "input"}`` and
    returning ``{"data":[{"index","embedding"}]}`` works: hosted APIs and
    self-hosted runtimes alike. Dimensions are validated against what the
    vector store can hold, and responses are checked to be finite, uniform
    and complete before anything is written.
    """

    def __init__(self, *, model: str, base_url: str, api_key: str | None = None,
                 timeout: float = 30.0, dimensions: int | None = None,
                 batch_size: int = 32):
        if not isinstance(model, str) or not model.strip() or timeout <= 0:
            raise ValueError("A model and positive timeout are required")
        if not isinstance(batch_size, int) or not 1 <= batch_size <= 256:
            raise ValueError("batch_size must be between 1 and 256")
        if dimensions is not None and dimensions <= 0:
            raise ValueError("dimensions must be positive when set")
        parsed = urlsplit(base_url)
        if (parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username or parsed.password
                or parsed.query or parsed.fragment
                or not parsed.path.rstrip("/").endswith("/embeddings")):
            raise ValueError(
                "base_url must be an http(s) URL whose path ends with /embeddings")
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.dimensions = dimensions
        self.batch_size = batch_size
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect())

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not isinstance(texts, list) or not texts or len(texts) > 2048:
            raise ValueError("1 to 2048 texts required")
        for text in texts:
            if not isinstance(text, str) or not text.strip() or len(text) > 8192:
                raise ValueError("Each text must be nonempty and at most 8192 characters")
        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            out.extend(self._embed_batch(texts[start:start + self.batch_size]))
        return out

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        payload = {"model": self.model, "input": batch}
        if self.dimensions is not None:
            payload["dimensions"] = self.dimensions
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(self.base_url, data=body,
                                         headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Embedding endpoint unavailable") from exc
        if len(raw) > 8 * 1024 * 1024:
            raise RuntimeError("Embedding response exceeds 8 MiB")
        try:
            outer = json.loads(raw)
            rows = outer["data"]
            ordered = sorted(rows, key=lambda row: row["index"])
            vectors = [row["embedding"] for row in ordered]
        except (ValueError, TypeError, KeyError) as exc:
            raise RuntimeError("Embedding endpoint returned an invalid JSON contract") from exc
        if len(vectors) != len(batch):
            raise RuntimeError("Embedding endpoint returned the wrong number of vectors")
        dimension = None
        for vector in vectors:
            if not isinstance(vector, list) or not vector:
                raise RuntimeError("Embedding vector must be a nonempty array")
            if dimension is None:
                dimension = len(vector)
            elif len(vector) != dimension:
                raise RuntimeError("Embedding vectors have inconsistent dimensions")
            for component in vector:
                if (not isinstance(component, (int, float)) or isinstance(component, bool)
                        or not math.isfinite(component)):
                    raise RuntimeError("Embedding vector contains non-finite values")
        if self.dimensions is not None and dimension != self.dimensions:
            raise RuntimeError(
                f"Embedding endpoint returned {dimension} dimensions, expected {self.dimensions}")
        # Only these widths have vector tables in the store schema.
        if dimension not in (384, 512):
            raise RuntimeError(
                f"Embedding dimension {dimension} is unsupported; the store has "
                "vector(384) and vector(512) tables. Request 384 or 512 dimensions.")
        return [[float(x) for x in vector] for vector in vectors]
