"""Typed synchronous HTTP client for third-party Agent integration.

Optional dependency: pip install agent-memory-core[api].
A MemoryClient never accesses PostgreSQL directly. In identity mode, pass the
workspace ID explicitly; the server validates it against the scoped credential.
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
    def __init__(self, base_url: str, api_key: str, *, tenant_id: str | None = None,
                 timeout: float = 15.0, transport=None):
        if not base_url or not api_key or timeout <= 0:
            raise ValueError("Base URL, API key, and positive timeout required")
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("Install agent-memory-core[api] to use MemoryClient") from exc
        if tenant_id is not None and (not isinstance(tenant_id, str) or not tenant_id.strip()):
            raise ValueError("tenant_id must be a non-empty string")
        headers={"Authorization":f"Bearer {api_key}"}
        if tenant_id is not None:
            headers["X-Memory-Tenant"]=tenant_id
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _request(self, method: str, path: str, *, params=None, json=None,
                 headers=None, content=None, raw: bool = False) -> Any:
        import httpx
        try:
            response = self._http.request(method,path,params=params,json=json,
                                          headers=headers,content=content)
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
        if raw:
            return response.content
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

    def revise(self, memory_id: str, *, subject_id: str, predicate: str,
               value: dict, source_id: str, valid_from: str,
               idempotency_key: str | None = None):
        """Create a new canonical version and supersede the supplied claim."""
        return self.remember(
            subject_id=subject_id, predicate=predicate, value=value,
            source_id=source_id, valid_from=valid_from, replaces_id=memory_id,
            idempotency_key=idempotency_key,
        )

    def recall(self, subject_id: str, *, predicate: str | None = None,
               valid_at: str | None = None, known_at: str | None = None):
        params={k:v for k,v in {"predicate":predicate,
                 "valid_at":valid_at,"known_at":known_at}.items() if v is not None}
        return self._request("GET",f"/v1/memories/{self._segment(subject_id)}",params=params)

    def process(self, *, text: str, source_id: str,
                candidates: list[dict] | None = None, confidence_threshold: float = 0.65,
                context_turns: list[str] | None = None,
                ground_with_memory: bool = False):
        """Process text; optionally ground model revisions in current tenant memory.

        Grounding may send existing memory evidence to the configured LLM
        provider. Enable only when its data-handling policy is appropriate.
        """
        if ground_with_memory and candidates is not None:
            raise ValueError("ground_with_memory cannot be combined with candidates")
        body={"text":text,"source_id":source_id,
              "confidence_threshold":confidence_threshold}
        if candidates is not None:
            body["candidates"]=candidates
        if context_turns is not None:
            body["context_turns"]=context_turns
        if ground_with_memory:
            body["ground_with_memory"]=True
        return self._request("POST","/v1/memories/process",json=body)

    def search(self, *, query: str, limit: int = 10,
               embedding: list[float] | None = None, model_id: str | None = None,
               anchor_entity: str | None = None):
        body={"query":query,"limit":limit}
        body.update({k:v for k,v in {"embedding":embedding,"model_id":model_id,
                                     "anchor_entity":anchor_entity}.items()
                     if v is not None})
        return self._request("POST","/v1/search",json=body)

    def upload_object(self, data: bytes, *,
                      content_type: str = "application/octet-stream"):
        """Store tenant-owned binary evidence (maximum 10 MiB)."""
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        return self._request("PUT","/v1/objects",content=data,
                             headers={"Content-Type":content_type})

    def download_object(self, sha256: str) -> bytes:
        """Read a tenant-owned SHA-256 evidence object as raw bytes."""
        if not isinstance(sha256,str) or len(sha256)!=64 or any(
                c not in "0123456789abcdefABCDEF" for c in sha256):
            raise ValueError("sha256 must be a 64-character hex digest")
        return self._request("GET","/v1/objects/"+sha256.lower(),raw=True)

    def predicate_policies(self):
        return self._request("GET","/v1/predicate-policies")

    def set_predicate_policy(self, predicate: str, *, cardinality: str):
        if cardinality not in ("one","many"):
            raise ValueError("cardinality must be one or many")
        return self._request("PUT","/v1/predicate-policies/"+self._segment(predicate),
                             json={"cardinality":cardinality})

    def resolve_entity(self, name: str):
        return self._request("GET","/v1/entities/resolve",params={"name":name})

    def observations(self, device_id: str, *, property: str, limit: int = 100,
                     start_at: str | None = None, end_at: str | None = None):
        params={"property":property,"limit":limit}
        if start_at is not None:
            params["start_at"]=start_at
        if end_at is not None:
            params["end_at"]=end_at
        return self._request("GET","/v1/observations/"+self._segment(device_id),
                             params=params)

    def aggregate_observations(self, device_id: str, *, property: str,
                               start_at: str, end_at: str):
        return self._request("GET","/v1/observations/"+self._segment(device_id)+"/aggregate",
                             params={"property":property,"start_at":start_at,"end_at":end_at})

    def search_config(self):
        return self._request("GET","/v1/evaluation/search-config")

    def whoami(self):
        """Return current principal, tenant and role when identity is enabled."""
        return self._request("GET", "/v1/identity/me")

    def workspaces(self):
        """List workspaces available to the current identity credential."""
        return self._request("GET", "/v1/identity/tenants")

    def list_memories(self, *, limit: int = 100,
                      include_superseded: bool = False,
                      subject_id: str | None = None,
                      predicate: str | None = None):
        """Browse canonical claims and optionally retain superseded history."""
        params={"limit":limit,"include_superseded":include_superseded}
        if subject_id is not None:
            params["subject_id"]=subject_id
        if predicate is not None:
            params["predicate"]=predicate
        return self._request("GET", "/v1/memories", params=params)

    def prepare_context(self, *, task_id: str, subject_ids: list[str],
                        token_budget: int = 2048):
        """Build a bounded context pack; no task state is mutated."""
        return self._request("POST", "/v1/context/prepare", json={
            "task_id": task_id, "subject_ids": subject_ids,
            "token_budget": token_budget,
        })

    def create_handoff(self, *, task_id: str, source_agent: str,
                       target_agent: str, subject_ids: list[str],
                       token_budget: int = 2048):
        """Freeze one immutable context snapshot; agent labels are descriptive."""
        return self._request("POST", "/v1/handoffs", json={
            "task_id": task_id, "source_agent": source_agent,
            "target_agent": target_agent, "subject_ids": subject_ids,
            "token_budget": token_budget,
        })

    def accept_handoff(self, handoff_id: str, *, target_agent: str):
        """Validate snapshot freshness; never start/retry external work."""
        return self._request(
            "POST", "/v1/handoffs/" + self._segment(handoff_id) + "/accept",
            json={"target_agent": target_agent},
        )

    def create_task(self, *, task_type: str, input: dict,
                    workflow_id: str | None = None,
                    idempotency_key: str | None = None):
        body={"task_type":task_type,"input":input}
        if workflow_id is not None:
            body["workflow_id"]=workflow_id
        if idempotency_key is not None:
            body["idempotency_key"]=idempotency_key
        return self._request("POST","/v1/tasks",json=body)

    def list_tasks(self, *, limit: int = 50, offset: int = 0):
        return self._request("GET","/v1/tasks",params={"limit":limit,"offset":offset})

    def get_task(self, task_id: str):
        return self._request("GET","/v1/tasks/"+self._segment(task_id))

    def get_task_effects(self, task_id: str):
        """Read redacted effect status; never returns raw device requests or tokens."""
        return self._request("GET", "/v1/tasks/" + self._segment(task_id) + "/effects")

    def claim_task(self, *, worker_id: str, lease_seconds: int = 30):
        """Claim a queued/expired task with a dedicated tasks:execute credential.

        The returned task contains the server fencing epoch; never invent one.
        """
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("A non-empty worker_id is required")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be 1..3600")
        return self._request("POST", "/v1/tasks/worker/claim", json={
            "worker_id": worker_id, "lease_seconds": lease_seconds})

    def renew_task_lease(self, task_id: str, *, worker_id: str,
                         lease_epoch: int, lease_seconds: int = 30):
        """Renew an existing lease; stale epochs are rejected by the server."""
        self._validate_worker_lease(worker_id, lease_epoch)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be 1..3600")
        return self._request("POST", "/v1/tasks/" + self._segment(task_id) +
                             "/lease/renew", json={"worker_id": worker_id,
                             "lease_epoch": lease_epoch, "lease_seconds": lease_seconds})

    @staticmethod
    def _validate_worker_lease(worker_id: str, lease_epoch: int) -> None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("A non-empty worker_id is required")
        if type(lease_epoch) is not int or lease_epoch < 1:
            raise ValueError("A positive integer lease_epoch is required")

    def commit_task_checkpoint(self, task_id: str, *, worker_id: str,
                               lease_epoch: int, expected_version: int,
                               checkpoint: dict, idempotency_key: str,
                               progress_completed: int = 0, progress_total: int = 0,
                               artifact_sha256: str | None = None,
                               effect_key: str | None = None,
                               effect_request: dict | None = None):
        """Fenced checkpoint; effect intent must be durable before execution."""
        self._validate_worker_lease(worker_id, lease_epoch)
        body={"worker_id": worker_id, "lease_epoch": lease_epoch,
              "expected_version":expected_version,"checkpoint":checkpoint,
              "idempotency_key":idempotency_key,
              "progress_completed":progress_completed,"progress_total":progress_total}
        for key,value in {"artifact_sha256":artifact_sha256,
                          "effect_key":effect_key,"effect_request":effect_request}.items():
            if value is not None:
                body[key]=value
        return self._request("POST","/v1/tasks/"+self._segment(task_id)+"/checkpoints",
                             json=body,headers={"Idempotency-Key":idempotency_key})

    def set_task_status(self, task_id: str, *, worker_id: str,
                        lease_epoch: int, status: str,
                        result: dict | None = None, error: dict | None = None):
        """Fenced terminal transition; never treat a memory receipt as completion."""
        self._validate_worker_lease(worker_id, lease_epoch)
        return self._request("POST","/v1/tasks/"+self._segment(task_id)+"/status",
                             json={"worker_id":worker_id,"lease_epoch":lease_epoch,
                                   "status":status,"result":result,"error":error})

    def verify_memory_async(self, memory_id: str, *, workflow_id: str | None = None):
        """Start a durable Temporal verification on the configured task queue."""
        body={"memory_id":self._segment(memory_id)}
        if workflow_id is not None:
            body["workflow_id"]=workflow_id
        return self._request("POST", "/v1/workflows/memory-verifications", json=body)

    def workflow_status(self, workflow_id: str):
        return self._request("GET",f"/v1/workflows/{self._segment(workflow_id)}")

    def cancel_workflow(self, workflow_id: str):
        return self._request("POST",f"/v1/workflows/{self._segment(workflow_id)}/cancel")

    def workflow_progress(self, workflow_id: str):
        """Read durable stage, pause state and completed-step count."""
        return self._request("GET",f"/v1/workflows/{self._segment(workflow_id)}/progress")

    def pause_workflow(self, workflow_id: str):
        """Request cooperative pause at the next safe workflow stage boundary."""
        return self._request("POST",f"/v1/workflows/{self._segment(workflow_id)}/pause")

    def resume_workflow(self, workflow_id: str):
        """Resume a workflow paused at a safe stage boundary."""
        return self._request("POST",f"/v1/workflows/{self._segment(workflow_id)}/resume")

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
