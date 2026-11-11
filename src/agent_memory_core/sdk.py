"""Typed synchronous HTTP client for third-party Agent integration.

Optional dependency: pip install agent-memory-core[api].
A MemoryClient never accesses PostgreSQL directly; tenant comes from server key.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote


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

    def _request(self, method: str, path: str, *, params=None, json=None, headers=None) -> Any:
        import httpx
        try:
            response = self._http.request(method,path,params=params,json=json,headers=headers)
        except httpx.TimeoutException as exc:
            raise MemoryAPIError(0,"TIMEOUT","Memory service request timed out") from exc
        except httpx.RequestError as exc:
            raise MemoryAPIError(0,"TRANSPORT_ERROR","Memory service transport unavailable") from exc
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
        try:
            return response.json()
        except ValueError as exc:
            raise MemoryAPIError(response.status_code,"INVALID_RESPONSE",
                                 "Memory service returned invalid JSON") from exc

    @staticmethod
    def _segment(identifier: str) -> str:
        if not isinstance(identifier,str) or not identifier or "/" in identifier or "\\" in identifier or identifier in (".",".."):
            raise ValueError("Path identifier must be a non-empty single segment")
        return quote(identifier,safe="")

    def ready(self):
        return self._request("GET","/v1/ready")

    def capabilities(self):
        return self._request("GET","/v1/capabilities")

    def remember(self, *, subject_id: str, predicate: str, value: dict,
                 source_id: str, valid_from: str, valid_to: str | None = None,
                 replaces_id: str | None = None, object_sha256: str | None = None,
                 idempotency_key: str | None = None):
        body = dict(subject_id=subject_id,predicate=predicate,value=value,
                    source_id=source_id,valid_from=valid_from)
        body.update({key:val for key,val in {
            "valid_to":valid_to,"replaces_id":replaces_id,
            "object_sha256":object_sha256,
        }.items() if val is not None})
        return self._request("POST","/v1/memories",json=body,
                             headers={"Idempotency-Key":idempotency_key} if idempotency_key is not None else None)

    def recall(self, subject_id: str, *, predicate: str | None = None,
               valid_at: str | None = None, known_at: str | None = None):
        params={k:v for k,v in {"predicate":predicate,
                 "valid_at":valid_at,"known_at":known_at}.items() if v is not None}
        return self._request("GET",f"/v1/memories/{self._segment(subject_id)}",params=params)

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
        return self._request("GET",f"/v1/memories/{self._segment(memory_id)}/sources")


    def store_embedding(self, memory_id: str, *, model_id: str,
                        embedding: list[float]):
        return self._request("PUT",f"/v1/memories/{self._segment(memory_id)}/embedding",
                             json={"model_id":model_id,"embedding":embedding})

    def add_entity(self, *, entity_id: str, entity_type: str,
                   canonical_name: str, aliases: list[str] | None = None,
                   attributes: dict | None = None):
        return self._request("POST","/v1/entities",json={
            "entity_id":entity_id,"entity_type":entity_type,
            "canonical_name":canonical_name,"aliases":aliases or [],
            "attributes":attributes or {}})

    def add_edge(self, *, from_entity: str, predicate: str,
                 to_entity: str, source_memory_id: str | None = None):
        body={"from_entity":from_entity,"predicate":predicate,"to_entity":to_entity}
        if source_memory_id is not None:
            body["source_memory_id"]=source_memory_id
        return self._request("POST","/v1/edges",json=body)

    def observe(self, *, device_id: str, source_event_id: str, observed_at: str,
                property: str, value: float, unit: str, source_id: str):
        return self._request("POST","/v1/observations",json={
            "device_id":device_id,"source_event_id":source_event_id,
            "observed_at":observed_at,"property":property,"value":value,
            "unit":unit,"source_id":source_id})

    def forget(self, memory_id: str):
        """Soft-forget one claim; audit history is retained server-side."""
        return self._request("DELETE",f"/v1/memories/{self._segment(memory_id)}")

    def forget_subject(self, *, subject_id: str, predicate: str | None = None):
        """Soft-forget every active claim of a subject (optionally one predicate)."""
        params={"subject_id":subject_id}
        if predicate is not None:
            params["predicate"]=predicate
        return self._request("DELETE","/v1/memories",params=params)

    def stream_events(self, *, after: str | None = None,
                      max_seconds: int = 30):
        """Yield SSE event dicts. Reconnect by passing last yielded id.

        Server bounds stream lifetime; caller controls reconnect/backoff.
        """
        if not 1 <= max_seconds <= 30:
            raise ValueError("max_seconds must be 1..30")
        params={"max_seconds":max_seconds}
        if after is not None:
            params["after"]=after
        with self._http.stream("GET","/v1/events/stream",params=params) as response:
            if response.is_error:
                raise MemoryAPIError(response.status_code,"HTTP_ERROR",
                                     response.read().decode("utf-8",errors="replace")[:500])
            frame={}
            for line in response.iter_lines():
                if not line:
                    if frame and "data" in frame:
                        import json
                        try:
                            body=json.loads(frame["data"])
                        except ValueError as exc:
                            raise MemoryAPIError(502,"INVALID_EVENT","Malformed SSE JSON") from exc
                        if frame.get("event")=="error":
                            raise MemoryAPIError(502,"EVENT_STREAM_ERROR",str(body))
                        yield {"id":frame.get("id"),"event":frame.get("event"),
                               "data":body}
                    frame={}
                    continue
                if line.startswith(":"):
                    continue
                name,separator,value=line.partition(":")
                if separator and name in ("id","event","data"):
                    frame[name]=value.lstrip(" ")


    def create_webhook(self, *, target_url: str, event_types: list[str]):
        """Create a callback and return its one-time signing secret."""
        return self._request("POST","/v1/webhooks",
                             json={"target_url":target_url,"event_types":event_types})

    def list_webhooks(self):
        return self._request("GET","/v1/webhooks")

    def revoke_webhook(self, subscription_id: str):
        return self._request("DELETE",f"/v1/webhooks/{self._segment(subscription_id)}")

    def webhook_deliveries(self, subscription_id: str, *, limit: int = 100):
        return self._request("GET",f"/v1/webhooks/{self._segment(subscription_id)}/deliveries",
                             params={"limit":limit})

    def replay_webhook_delivery(self, delivery_id: str):
        return self._request("POST",f"/v1/webhooks/deliveries/{self._segment(delivery_id)}/replay")

    def replay_outbox_event(self, event_id: str):
        """Requeue a dead outbox event owned by this API key's tenant."""
        return self._request("POST",f"/v1/events/outbox/{self._segment(event_id)}/replay")
