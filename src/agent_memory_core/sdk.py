"""Typed synchronous HTTP client for third-party Agent integration.

Optional dependency: pip install agent-memory-core[api].
A MemoryClient never accesses PostgreSQL directly; tenant comes from server key.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MemoryAPIError(Exception):
    status_code: int
    code: str
    detail: str

    def __str__(self):
        return f"{self.code} ({self.status_code}): {self.detail}"


class MemoryClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 15.0,
                 transport=None):
        if not base_url or not api_key or timeout <= 0:
            raise ValueError("Base URL, API key, and positive timeout required")
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("Install agent-memory-core[api] to use MemoryClient") from exc
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization":f"Bearer {api_key}"},
            timeout=timeout,transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _request(self, method: str, path: str, *, params=None, json=None) -> Any:
        response = self._http.request(method,path,params=params,json=json)
        if response.is_error:
            try:
                body = response.json()
                detail = body.get("detail",body) if isinstance(body,dict) else body
            except ValueError:
                detail = response.text[:500]
            code = {
                400:"BAD_REQUEST",401:"UNAUTHENTICATED",403:"FORBIDDEN",
                404:"NOT_FOUND",409:"CONFLICT",422:"VALIDATION_ERROR",
                429:"RATE_LIMITED",503:"UNAVAILABLE",
            }.get(response.status_code,"HTTP_ERROR")
            raise MemoryAPIError(response.status_code,code,str(detail))
        if not response.content:
            return None
        return response.json()

    def ready(self):
        return self._request("GET","/v1/ready")

    def capabilities(self):
        return self._request("GET","/v1/capabilities")

    def remember(self, *, subject_id: str, predicate: str, value: dict,
                 source_id: str, valid_from: str, valid_to: str | None = None,
                 replaces_id: str | None = None, object_sha256: str | None = None):
        body = dict(subject_id=subject_id,predicate=predicate,value=value,
                    source_id=source_id,valid_from=valid_from)
        body.update({key:val for key,val in {
            "valid_to":valid_to,"replaces_id":replaces_id,
            "object_sha256":object_sha256,
        }.items() if val is not None})
        return self._request("POST","/v1/memories",json=body)

    def recall(self, subject_id: str, *, predicate: str | None = None,
               valid_at: str | None = None, known_at: str | None = None):
        params={k:v for k,v in {"predicate":predicate,
                 "valid_at":valid_at,"known_at":known_at}.items() if v is not None}
        return self._request("GET",f"/v1/memories/{subject_id}",params=params)

    def process(self, *, text: str, source_id: str,
                candidates: list[dict] | None = None, confidence_threshold: float = 0.65):
        body={"text":text,"source_id":source_id,
              "confidence_threshold":confidence_threshold}
        if candidates is not None:
            body["candidates"]=candidates
        return self._request("POST","/v1/memories/process",json=body)

    def search(self, *, query: str, limit: int = 10,
               embedding: list[float] | None = None, model_id: str | None = None,
               anchor_entity: str | None = None):
        body={"query":query,"limit":limit}
        body.update({k:v for k,v in {"embedding":embedding,"model_id":model_id,
                                     "anchor_entity":anchor_entity}.items()
                     if v is not None})
        return self._request("POST","/v1/search",json=body)

    def events(self, *, after: str | None = None, limit: int = 100):
        params={"limit":limit}
        if after is not None:
            params["after"]=after
        return self._request("GET","/v1/events",params=params)

    def sources(self, memory_id: str):
        return self._request("GET",f"/v1/memories/{memory_id}/sources")
