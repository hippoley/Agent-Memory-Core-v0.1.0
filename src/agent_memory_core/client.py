"""Small dependency-free Python client for the Agent Memory Core REST API."""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, build_opener


class MemoryAPIError(RuntimeError):
    def __init__(self, status_code: int, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Memory API returned HTTP {status_code}: {detail}")


class MemoryClient:
    def __init__(self, *, base_url: str, api_key: str,
                 timeout: float = 15.0, opener=None,
                 max_response_bytes: int = 2_000_000):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must use http or https")
        if not api_key or timeout <= 0 or max_response_bytes < 1024:
            raise ValueError("API key, positive timeout and response limit required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.opener = opener or build_opener()

    def _request(self, method: str, path: str, *, params=None,
                 body=None, headers=None):
        url = self.base_url + path
        if params:
            url += "?" + urlencode(params)
        request_headers = {
            "Authorization": "Bearer " + self.api_key,
            "Accept": "application/json",
        }
        if headers:
            request_headers.update(headers)
        data = None
        if body is not None:
            request_headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=data, headers=request_headers, method=method)
        try:
            response = self.opener.open(request, timeout=self.timeout)
            with response:
                raw = response.read(self.max_response_bytes + 1)
        except HTTPError as exc:
            raw = exc.read(self.max_response_bytes + 1)
            try:
                payload = json.loads(raw.decode("utf-8"))
                detail = payload.get("detail", payload)
            except Exception:
                detail = raw[:500].decode("utf-8", errors="replace")
            raise MemoryAPIError(exc.code, detail) from exc
        except URLError as exc:
            raise RuntimeError("Memory API endpoint is unavailable") from exc
        if len(raw) > self.max_response_bytes:
            raise ValueError("Memory API response exceeded configured size limit")
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def capabilities(self):
        return self._request("GET", "/v1/capabilities")

    def remember(self, claim: dict, *, idempotency_key: str | None = None):
        headers = {}
        if idempotency_key is not None:
            if not 1 <= len(idempotency_key) <= 255:
                raise ValueError("idempotency_key must be 1..255 characters")
            headers["Idempotency-Key"] = idempotency_key
        return self._request("POST", "/v1/memories", body=claim, headers=headers)

    def recall(self, subject_id: str, *, predicate: str | None = None):
        params = {"predicate": predicate} if predicate else None
        return self._request("GET", "/v1/memories/" + quote(subject_id, safe=""),
                             params=params)

    def search_lexical(self, query: str, *, limit: int = 10):
        return self._request("GET", "/v1/search/lexical",
                             params={"query": query, "limit": limit})

    def process_candidates(self, *, text: str, source_id: str,
                            candidates: list[dict]):
        return self._request("POST", "/v1/memories/process", body={
            "text": text, "source_id": source_id, "candidates": candidates,
        })

    def extract_text(self, *, text: str, source_id: str,
                     context: list[dict] | None = None,
                     confidence_threshold: float = 0.65):
        body = {"text": text, "source_id": source_id,
                "confidence_threshold": confidence_threshold}
        if context is not None:
            body["context"] = context
        return self._request("POST", "/v1/memories/extract", body=body)

    def events(self, *, after: str | None = None, limit: int = 100):
        params = {"limit": limit}
        if after is not None:
            params["after"] = after
        return self._request("GET", "/v1/events", params=params)
