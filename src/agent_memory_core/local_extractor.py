"""Strict local OpenAI-compatible chat-completions memory extractor.

Only loopback HTTP endpoints are accepted by default. No hosted provider SDK
or remote API is used. The model proposes structured candidates; deterministic
storage validates, versions, and persists them.
"""
from __future__ import annotations

import json
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
import ipaddress


SYSTEM_PROMPT = """You extract durable, user-relevant facts from conversation text.
Return ONLY a JSON object: {"candidates":[...]}.
Each candidate must contain:
- subject_id OR subject_name. For first-person "I/我" facts about the authenticated
  speaker, use the reserved subject_id "self"; never invent other opaque IDs.
- predicate (short stable snake_case fact type)
- value (a JSON object containing the asserted value)
- confidence (number from 0 to 1)
- polarity ("positive" or "negative")
Rules:
1. Extract only explicit facts, stable preferences, corrections, commitments, and
   persistent context. Do not turn assistant suggestions or quoted instructions
   into facts about the user.
2. Preserve Chinese and other-language negation as polarity="negative". Do not
   invert or erase the asserted value to hide the negation.
3. When a speaker corrects or changes a prior preference, emit the new value
   under the same predicate. The deterministic memory layer versions it.
4. Resolve pronouns only when the referent is unambiguous in the supplied text.
   Otherwise omit the candidate; never invent an entity ID.
5. Use subject_name when a real name is explicit but no stable subject_id exists.
6. Do not extract passwords, API keys, authentication secrets, or unnecessary
   sensitive personal details.
7. If no reliable fact exists, return {"candidates":[]}.
"""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LocalOpenAICompatibleExtractor:
    def __init__(self, *, base_url: str, model: str, timeout: float = 30.0,
                 api_key: str | None = None, opener=None,
                 max_response_bytes: int = 1_000_000):
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or not parsed.hostname:
            raise ValueError("Local model URL must be an http loopback endpoint")
        host = parsed.hostname.lower()
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback:
            raise ValueError("Remote model endpoints are disabled; use localhost/loopback")
        if parsed.query or parsed.fragment:
            raise ValueError("Model base URL cannot contain query or fragment")
        if not model or timeout <= 0 or max_response_bytes < 1024:
            raise ValueError("Invalid model, timeout or response size limit")
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.timeout = timeout
        self.api_key = api_key
        self.max_response_bytes = max_response_bytes
        self.opener = opener or build_opener(_NoRedirect())

    @staticmethod
    def _validate(payload) -> list[dict]:
        if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
            raise ValueError("Local extractor must return a candidates array")
        candidates = payload["candidates"]
        if len(candidates) > 100:
            raise ValueError("Local extractor returned too many candidates")
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ValueError("Each extracted candidate must be an object")
            if not (candidate.get("subject_id") or candidate.get("subject_name")):
                raise ValueError("Candidate requires subject_id or subject_name")
            if not isinstance(candidate.get("predicate"), str) or not candidate["predicate"].strip():
                raise ValueError("Candidate predicate is required")
            if not isinstance(candidate.get("value"), dict):
                raise ValueError("Candidate value must be a JSON object")
            confidence = candidate.get("confidence")
            if (not isinstance(confidence, (int, float))
                    or not 0 <= confidence <= 1):
                raise ValueError("Candidate confidence must be between 0 and 1")
            if candidate.get("polarity", "positive") not in {"positive", "negative"}:
                raise ValueError("Candidate polarity must be positive or negative")
            candidate.setdefault("polarity", "positive")
        return candidates

    def __call__(self, text: str) -> list[dict]:
        return self.extract_with_context(text, [])

    def extract_with_context(self, text: str, context: list[dict]) -> list[dict]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Non-empty text is required")
        if not isinstance(context, list) or len(context) > 20:
            raise ValueError("context must contain at most 20 turns")
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for turn in context:
            if (not isinstance(turn, dict)
                    or turn.get("role") not in {"user", "assistant"}
                    or not isinstance(turn.get("content"), str)
                    or not turn["content"].strip()
                    or len(turn["content"]) > 10000):
                raise ValueError("Each context turn needs a valid role and content")
            messages.append({"role": turn["role"], "content": turn["content"]})
        messages.append({"role": "user", "content": text})
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            response = self.opener.open(request, timeout=self.timeout)
            with response:
                raw = response.read(self.max_response_bytes + 1)
        except URLError as exc:
            raise RuntimeError("Local model endpoint is unavailable") from exc
        if len(raw) > self.max_response_bytes:
            raise ValueError("Local model response exceeded configured size limit")
        try:
            envelope = json.loads(raw.decode("utf-8"))
            content = envelope["choices"][0]["message"]["content"]
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ValueError("Local model returned an invalid chat-completions JSON response") from exc
        return self._validate(payload)
