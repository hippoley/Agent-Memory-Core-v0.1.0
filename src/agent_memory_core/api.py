"""Local-first REST gateway. Bind to loopback unless deployed behind a hardened gateway."""
from __future__ import annotations

import hmac
import os
import asyncio
import json

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .contracts import CONTRACT_VERSION, aware_iso, capabilities
from .api_models import (HealthResponse, ReadinessResponse, CapabilitiesResponse, MemoryWriteResponse,
                         MemoryReadResponse, SearchResponse)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_id: str = Field(min_length=1, max_length=255, description="Stable entity or subject identifier.")
    predicate: str = Field(min_length=1, max_length=255, description="Fact type, e.g. preference, has_window, or device_state.")
    value: dict = Field(description="Structured JSON fact payload.")
    source_id: str = Field(min_length=1, max_length=255, description="Provenance identifier, e.g. user utterance, sensor event, or document.")
    valid_from: str = Field(description="Timezone-aware ISO-8601 timestamp; inclusive real-world validity start.")
    valid_to: str | None = Field(default=None, description="Timezone-aware ISO-8601 exclusive validity end.")
    replaces_id: str | None = Field(default=None, description="Existing claim UUID to correct; subject and predicate must match.")
    object_sha256: str | None = Field(default=None, description="Optional SHA-256 reference to separately stored evidence object.")


class ObservationWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(min_length=1, max_length=255)
    source_event_id: str = Field(min_length=1, max_length=255)
    observed_at: str = Field(description="Timezone-aware ISO-8601 physical event time.")
    property: str = Field(min_length=1, max_length=255)
    value: float
    unit: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=255)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)
    embedding: list[float] | None = Field(default=None, description="Caller-generated query embedding. Supported dimensions depend on configured model profile.")
    model_id: str | None = Field(default=None, description="Required when embedding is supplied.")
    anchor_entity: str | None = Field(default=None, description="Optional graph entity identifier for AGE neighborhood expansion.")
    limit: int = Field(default=10, ge=1, le=100)
    valid_at: str | None = Field(default=None, description="Timezone-aware ISO-8601 world-time filter.")
    known_at: str | None = Field(default=None, description="Timezone-aware ISO-8601 knowledge-time filter.")


def create_app(*, api_key=None, tenant_id=None, store_factory=None,
               search_factory=None, object_store_factory=None, capability_flags=None):
    """Create a single-tenant app. Tenant identity is bound server-side to the API key."""
    key = api_key if api_key is not None else os.getenv("MEMORY_API_KEY")
    tenant = tenant_id if tenant_id is not None else os.getenv("MEMORY_TENANT_ID")
    if not key or len(key) < 24 or not tenant:
        raise RuntimeError("MEMORY_API_KEY (24+ chars) and MEMORY_TENANT_ID required")

    if store_factory is None:
        def store_factory():
            from .postgres import PostgresMemory
            return PostgresMemory(os.environ["MEMORY_DSN"])

    app = FastAPI(
        title="Agent Memory Core API",
        version=CONTRACT_VERSION,
        description=(
            "Local-first, evidence-aware Agent memory. PostgreSQL is the canonical "
            "record; pgvector and Apache AGE are optional retrieval indexes; object "
            "storage and Temporal are independent optional adapters. The currently "
            "implemented HTTP surface is claims, recall, capability discovery and "
            "configured hybrid search. Unimplemented adapters are not implied by this API."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        openapi_tags=[
            {"name": "System", "description": "Health and runtime capability discovery."},
            {"name": "Memory", "description": "Canonical claim write and bitemporal recall."},
            {"name": "Retrieval", "description": "Optional SQL + vector + graph hybrid search."},
            {"name": "Objects", "description": "Content-addressed evidence object upload and download."},
            {"name": "Observations", "description": "Idempotent device observations with event-time queries."},
            {"name": "Events", "description": "Durable tenant-scoped event replay with cursor pagination."},
        ],
    )
    bearer = HTTPBearer(
        auto_error=False,
        description="API key issued by the service operator. Send: Authorization: Bearer <key>.",
    )

    def auth(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=401, detail="Bearer token required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not hmac.compare_digest(credentials.credentials, key):
            raise HTTPException(status_code=403, detail="Invalid credential")
        return tenant

    @app.get(
        "/v1/health", tags=["System"], summary="Health check", response_model=HealthResponse,
        description="Unauthenticated liveness only. Does not prove database, graph, vector or Temporal readiness.",
    )
    def health():
        return {"status": "alive", "contract_version": CONTRACT_VERSION}

    @app.get(
        "/v1/ready", tags=["System"], summary="PostgreSQL readiness",
        response_model=ReadinessResponse,
        description="Authenticated readiness check against the canonical PostgreSQL store only. Does not imply AGE, embedding models, object store or Temporal availability.",
        responses={503: {"description": "Canonical PostgreSQL store is unavailable."}},
    )
    def ready(t: str = Depends(auth)):
        try:
            db = store_factory()
            try:
                ok = db.conn.execute("SELECT 1").fetchone()
                if not ok or ok[0] != 1:
                    raise RuntimeError("Unhealthy database response")
            finally:
                db.close()
        except Exception:
            raise HTTPException(status_code=503, detail="Canonical memory store is unavailable")
        return {"status": "ready", "checks": {"postgresql": "ready"},
                "contract_version": CONTRACT_VERSION}

    @app.get(
        "/v1/capabilities", tags=["System"], summary="Discover enabled capabilities", response_model=CapabilitiesResponse,
        description="Returns configured capability flags. Treat unavailable capabilities as unsupported on this instance.",
    )
    def get_capabilities(t: str = Depends(auth)):
        return {
            "contract_version": CONTRACT_VERSION,
            "tenant_id": t,
            "capabilities": capabilities(**(capability_flags or {})),
        }

    @app.post(
        "/v1/memories", status_code=201, tags=["Memory"], summary="Store a memory claim",
        response_model=MemoryWriteResponse,
        description=(
            "Writes a provenance-aware claim to the canonical store. valid_from/valid_to "
            "describe real-world validity; valid_to is exclusive. To correct a claim, "
            "supply replaces_id for an active claim with the same subject and predicate. "
            "Tenant identity is derived from the API credential, never from request JSON."
        ),
    )
    def add_claim(item: Claim, t: str = Depends(auth)):
        try:
            valid_from = aware_iso(item.valid_from)
            valid_to = aware_iso(item.valid_to)
            db = store_factory()
            try:
                mid = db.remember(
                    tenant_id=t, subject_id=item.subject_id, predicate=item.predicate,
                    value=item.value, source_id=item.source_id, valid_from=valid_from,
                    valid_to=valid_to, replaces_id=item.replaces_id,
                    object_sha256=item.object_sha256,
                )
            finally:
                db.close()
            return {"id": mid, "tenant_id": t}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get(
        "/v1/memories/{subject_id}", tags=["Memory"], summary="Recall claims for a subject",
        response_model=MemoryReadResponse,
        response_model_exclude_none=True,
        description=(
            "Returns claims valid at valid_at as known at known_at. Both timestamps "
            "must include a timezone. Omitted timestamps default to the current time."
        ),
    )
    def recall(subject_id: str, predicate: str | None = None,
               valid_at: str | None = None, known_at: str | None = None,
               t: str = Depends(auth)):
        try:
            va = aware_iso(valid_at)
            ka = aware_iso(known_at)
            db = store_factory()
            try:
                return {
                    "contract_version": CONTRACT_VERSION,
                    "items": db.recall(
                        tenant_id=t, subject_id=subject_id, predicate=predicate,
                        valid_at=va, known_at=ka,
                    ),
                }
            finally:
                db.close()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.put(
        "/v1/objects", tags=["Objects"], summary="Store an evidence object",
        responses={201: {"description": "Object stored; SHA-256 returned in X-Content-SHA256."},
                   413: {"description": "Object exceeds the configured size limit."},
                   503: {"description": "Object storage is not configured."}},
    )
    async def put_object(request: Request, t: str = Depends(auth)):
        if object_store_factory is None:
            raise HTTPException(status_code=503, detail="Object storage is not configured")
        data = await request.body()
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Object exceeds 10 MiB limit")
        try:
            store = object_store_factory()
            digest = store.put(data)
            return Response(status_code=201, headers={"X-Content-SHA256": digest},
                             media_type="application/json",
                             content='{"sha256":"' + digest + '","size":' + str(len(data)) + '}')
        except Exception:
            raise HTTPException(status_code=503, detail="Object storage write failed")

    @app.get(
        "/v1/objects/{digest}", tags=["Objects"], summary="Retrieve an evidence object",
        responses={404: {"description": "Object does not exist."},
                   503: {"description": "Object storage is not configured."}},
    )
    def get_object(digest: str, t: str = Depends(auth)):
        if object_store_factory is None:
            raise HTTPException(status_code=503, detail="Object storage is not configured")
        try:
            store = object_store_factory()
            data = store.get(digest)
            return Response(content=data, media_type="application/octet-stream",
                            headers={"X-Content-SHA256": digest})
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Object not found")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception:
            raise HTTPException(status_code=503, detail="Object storage read failed")

    @app.post(
        "/v1/observations", status_code=201, tags=["Observations"],
        summary="Record a device observation",
        description="Stores a tenant-scoped, idempotent observation. Repeated identical source_event_id returns 200; conflicting reuse is rejected.",
        responses={200: {"description": "Identical observation already existed."}},
    )
    def add_observation(item: ObservationWrite, t: str = Depends(auth)):
        try:
            db = store_factory()
            try:
                inserted = db.record_observation(
                    tenant_id=t, device_id=item.device_id,
                    source_event_id=item.source_event_id,
                    observed_at=aware_iso(item.observed_at), property=item.property,
                    value=item.value, unit=item.unit, source_id=item.source_id)
            finally:
                db.close()
            return Response(status_code=201 if inserted else 200,
                            media_type="application/json",
                            content='{"accepted":true,"duplicate":' + ('false' if inserted else 'true') + '}')
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.get(
        "/v1/observations/{device_id}", tags=["Observations"],
        summary="Query observations for a device",
    )
    def get_observations(device_id: str, property: str, limit: int = 100,
                         t: str = Depends(auth)):
        try:
            db = store_factory()
            try:
                items = db.observations(tenant_id=t, device_id=device_id,
                                        property=property, limit=limit)
            finally:
                db.close()
            return {"tenant_id": t, "device_id": device_id,
                    "property": property, "items": items}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get(
        "/v1/events", tags=["Events"], summary="Replay durable memory events",
        description="Polls the tenant-scoped durable event log. Pass the previous page's next_cursor as after to continue without losing events.",
    )
    def get_events(after: str | None = None, limit: int = 100,
                   t: str = Depends(auth)):
        try:
            db = store_factory()
            try:
                items = db.events_after(tenant_id=t, after=after, limit=limit)
            finally:
                db.close()
            return {"items": items,
                    "next_cursor": items[-1]["event_id"] if items else after,
                    "has_more": len(items) == limit}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get(
        "/v1/events/stream", tags=["Events"], summary="Subscribe to memory events (SSE)",
        description="Server-Sent Events stream. Supports Last-Event-ID or after cursor for replay after reconnect. Each connection is bounded to 30 seconds; reconnect with the last received event ID.",
    )
    async def stream_events(request: Request, after: str | None = None,
                            max_seconds: int = 30, t: str = Depends(auth)):
        if not 1 <= max_seconds <= 30:
            raise HTTPException(status_code=422, detail="max_seconds must be between 1 and 30")
        cursor = after or request.headers.get("last-event-id")
        async def generate():
            last_id = cursor
            loop = asyncio.get_running_loop()
            deadline = loop.time() + max_seconds
            while loop.time() < deadline and not await request.is_disconnected():
                try:
                    db = store_factory()
                    try:
                        items = await asyncio.to_thread(
                            db.events_after, tenant_id=t, after=last_id, limit=100)
                    finally:
                        await asyncio.to_thread(db.close)
                except ValueError as exc:
                    yield "event: error\\ndata: " + json.dumps({"detail":str(exc)}) + "\\n\\n"
                    return
                if items:
                    for item in items:
                        last_id = item["event_id"]
                        payload = {"event_id":last_id,"event_type":item["event_type"],
                                   "payload":item["payload"],"recorded_at":str(item["recorded_at"])}
                        yield "id: " + last_id + "\\nevent: " + item["event_type"] + "\\ndata: " + json.dumps(payload,default=str) + "\\n\\n"
                else:
                    yield ": keepalive\\n\\n"
                    await asyncio.sleep(0.5)
        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

    @app.post(
        "/v1/search", tags=["Retrieval"], summary="Hybrid memory search",
        response_model=SearchResponse,
        description=(
            "Queries an injected search adapter. Embeddings are generated by the caller; "
            "this service does not host an embedding model. Vector and AGE are optional "
            "channels. Returns 503 if no search adapter is configured. Optional-channel "
            "failures appear in degraded_channels; canonical SQL failures propagate."
        ),
        responses={503: {"description": "Hybrid search adapter is not configured."}},
    )
    def search_memories(item: SearchRequest, t: str = Depends(auth)):
        flags = capability_flags or {}
        if item.embedding is not None and not flags.get("semantic", False):
            raise HTTPException(status_code=503, detail="Vector retrieval is disabled on this instance")
        if item.anchor_entity is not None and not flags.get("graph", False):
            raise HTTPException(status_code=503, detail="Graph retrieval is disabled on this instance")
        if search_factory is None:
            raise HTTPException(status_code=503, detail="Hybrid search adapter is not configured")
        try:
            service = search_factory()
            try:
                report = service.search_detailed(
                    tenant_id=t, query=item.query, embedding=item.embedding,
                    model_id=item.model_id, anchor_entity=item.anchor_entity,
                    limit=item.limit, valid_at=aware_iso(item.valid_at),
                    known_at=aware_iso(item.known_at),
                )
                return {
                    "contract_version": CONTRACT_VERSION,
                    "items": [
                        {
                            "memory_id": h.memory_id, "subject_id": h.subject_id,
                            "predicate": h.predicate, "value": h.value,
                            "source_id": h.source_id, "score": h.score,
                            "channels": list(h.channels),
                        }
                        for h in report.hits
                    ],
                    "channels_used": list(report.channels_used),
                    "degraded_channels": report.degraded_channels,
                }
            finally:
                close = getattr(service, "close", None)
                if callable(close):
                    close()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    return app
