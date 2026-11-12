"""Local-first REST gateway. Bind to loopback unless deployed behind a hardened gateway."""
from __future__ import annotations

import hmac
import os
import asyncio
import json
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .contracts import CONTRACT_VERSION, aware_iso, capabilities
from .api_models import (HealthResponse, ReadinessResponse, CapabilitiesResponse, MemoryWriteResponse,
                         MemoryReadResponse, SearchResponse)


def require_uuid(memory_id: str) -> str:
    """Reject non-UUID path identifiers before they reach SQL.

    Without this a malformed id surfaces as a PostgreSQL
    InvalidTextRepresentation (HTTP 500) and tears down the keep-alive
    connection, which also fails the next request on that connection.
    """
    try:
        return str(UUID(memory_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(
            status_code=422, detail="memory_id must be a valid UUID") from exc


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


class WebhookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_url: str = Field(min_length=12,max_length=2048)
    event_types: list[str] = Field(min_length=1,max_length=50)


class ObservationWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(min_length=1, max_length=255)
    source_event_id: str = Field(min_length=1, max_length=255)
    observed_at: str = Field(description="Timezone-aware ISO-8601 physical event time.")
    property: str = Field(min_length=1, max_length=255)
    value: float
    unit: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=255)


class EmbeddingWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=1, max_length=255)
    embedding: list[float] = Field(min_length=384, max_length=512)


class EntityWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str = Field(min_length=1, max_length=255)
    entity_type: str
    canonical_name: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list)
    attributes: dict = Field(default_factory=dict)


class EdgeWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_entity: str = Field(min_length=1, max_length=255)
    predicate: str = Field(min_length=1, max_length=255)
    to_entity: str = Field(min_length=1, max_length=255)
    source_memory_id: str | None = None


class ProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=100000, description="Original text retained as processing context; source_id identifies its durable source.")
    source_id: str = Field(min_length=1, max_length=255, description="Provenance identifier for this ingestion.")
    candidates: list[dict] | None = Field(default=None, max_length=500, description="Optional structured facts. If omitted, a server-configured local extractor must be available.")
    confidence_threshold: float = Field(default=0.65, ge=0.0, le=1.0)


class MemoryVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_id: str = Field(min_length=36, max_length=36, description="Canonical memory claim UUID to verify.")
    workflow_id: str | None = Field(default=None, min_length=1, max_length=255, description="Optional stable Temporal workflow ID; generated if omitted.")


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
               search_factory=None, object_store_factory=None, capability_flags=None,
               temporal_client_factory=None, temporal_task_queue=None, memory_extractor_factory=None,
               embedder=None, auto_embed=True,
               webhook_signing_key=None, webhook_allowed_hosts=None):
    """Create a single-tenant app. Tenant identity is bound server-side to the API key.

    ``embedder`` is an optional provider-backed object exposing ``model_id``,
    ``embed_text(text) -> vector`` and ``__call__(candidate) -> (model_id, vector)``.
    When present and ``auto_embed`` is true, claims are vectorised on write and
    queries are vectorised on search, so the semantic channel works without the
    caller computing vectors. Provider failure never fails the canonical write.
    """
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
            "implemented HTTP surface includes memory claims and processing, evidence "
            "objects, observations, events, entity/edge operations, hybrid retrieval, "
            "and optional Temporal workflow lifecycle endpoints. Optional capabilities "
            "must be enabled and are reported through capability discovery."
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
            {"name": "Knowledge Graph", "description": "Canonical entity registry, relation writes and vector indexing."},
            {"name": "Workflows", "description": "Optional Temporal workflow lifecycle operations."},
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
        response_model_exclude_none=True,
        description=(
            "Writes a provenance-aware claim to the canonical store. valid_from/valid_to "
            "describe real-world validity; valid_to is exclusive. To correct a claim, "
            "supply replaces_id for an active claim with the same subject and predicate. "
            "Tenant identity is derived from the API credential, never from request JSON."
        ),
    )
    def add_claim(item: Claim, request: Request, t: str = Depends(auth)):
        from .postgres import IdempotencyConflict
        try:
            valid_from = aware_iso(item.valid_from)
            valid_to = aware_iso(item.valid_to)
            db = store_factory()
            try:
                kwargs=dict(
                    tenant_id=t, subject_id=item.subject_id, predicate=item.predicate,
                    value=item.value, source_id=item.source_id, valid_from=valid_from,
                    valid_to=valid_to, replaces_id=item.replaces_id,
                    object_sha256=item.object_sha256,
                )
                key=request.headers.get("Idempotency-Key")
                mid=(db.remember_idempotent(idempotency_key=key,**kwargs)
                     if key is not None else db.remember(**kwargs))
                response={"id":mid,"tenant_id":t}
                if embedder is not None and auto_embed:
                    try:
                        model_id,vector=embedder({"subject_id":item.subject_id,
                            "predicate":item.predicate,"value":item.value})
                        db.store_embedding(tenant_id=t,memory_id=mid,
                                           model_id=model_id,vector=vector)
                        response["embedded"]=True
                    except Exception:
                        # Canonical claim is committed; a projection worker can
                        # retry vector indexing. Vector failure must not fail a write.
                        response["embedded"]=False
            finally:
                db.close()
            return response
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409,detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc))

    @app.get(
        "/v1/memories", tags=["Memory"], summary="Browse tenant claims",
        response_model=MemoryReadResponse,
        response_model_exclude_none=True,
        description=(
            "Newest-first browse of canonical claims for the authenticated tenant. "
            "include_superseded=true keeps replaced and forgotten claims so the audit "
            "trail is visible; known_at answers 'what did we believe then'. "
            "Read-only: this endpoint never mutates state."
        ),
    )
    def list_memories(subject_id: str | None = None, predicate: str | None = None,
                      include_superseded: bool = False,
                      valid_at: str | None = None, known_at: str | None = None,
                      limit: int = 100, offset: int = 0,
                      t: str = Depends(auth)):
        try:
            db = store_factory()
            try:
                items = db.list_claims(
                    tenant_id=t, subject_id=subject_id, predicate=predicate,
                    include_superseded=include_superseded,
                    valid_at=aware_iso(valid_at), known_at=aware_iso(known_at),
                    limit=limit, offset=offset,
                )
            finally:
                db.close()
            return {"contract_version": CONTRACT_VERSION, "items": items,
                    "count": len(items), "limit": limit, "offset": offset}
        except (ValueError, TypeError) as exc:
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

    @app.delete(
        "/v1/memories/{memory_id}", tags=["Memory"],
        summary="Soft-forget one memory claim",
        description=(
            "Withdraws belief in the claim: it immediately disappears from "
            "active recall and search while the bitemporal audit history and "
            "the claim row are retained. Emits a durable "
            "memory.claim.forgotten event. This is not a hard delete; erasure "
            "compliance workflows are a separate concern."
        ),
    )
    def forget_memory(memory_id: str, t: str = Depends(auth)):
        memory_id = require_uuid(memory_id)
        db = store_factory()
        try:
            forgotten = db.forget(tenant_id=t, memory_id=memory_id)
        finally:
            db.close()
        if not forgotten:
            raise HTTPException(status_code=404, detail="Active claim not found")
        return {"forgotten": [memory_id], "audit_retained": True}

    @app.delete(
        "/v1/memories", tags=["Memory"],
        summary="Soft-forget active claims by subject/predicate filter",
        description=(
            "Bulk soft-forget of every active claim matching the optional "
            "subject_id and predicate filters; at least one filter is required. "
            "Each forgotten claim emits a durable memory.claim.forgotten event. "
            "Audit history is retained."
        ),
    )
    def forget_memories(subject_id: str | None = None, predicate: str | None = None,
                        t: str = Depends(auth)):
        if not subject_id and not predicate:
            raise HTTPException(status_code=422,
                                detail="Provide subject_id and/or predicate")
        db = store_factory()
        try:
            forgotten = db.forget_many(tenant_id=t, subject_id=subject_id,
                                       predicate=predicate)
        finally:
            db.close()
        return {"forgotten": forgotten, "count": len(forgotten), "audit_retained": True}

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
            db = store_factory()
            try:
                db.register_object(
                    tenant_id=t, digest=digest, byte_size=len(data),
                    content_type=request.headers.get("content-type","application/octet-stream"),
                )
            finally:
                db.close()
            return Response(status_code=201, headers={"X-Content-SHA256": digest},
                             media_type="application/json",
                             content='{"sha256":"' + digest + '","size":' + str(len(data)) + '}')
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception:
            raise HTTPException(status_code=503, detail="Object storage write or ownership registration failed")

    @app.get(
        "/v1/objects/{digest}", tags=["Objects"], summary="Retrieve an evidence object",
        responses={404: {"description": "Object does not exist."},
                   503: {"description": "Object storage is not configured."}},
    )
    def get_object(digest: str, t: str = Depends(auth)):
        if object_store_factory is None:
            raise HTTPException(status_code=503, detail="Object storage is not configured")
        try:
            db = store_factory()
            try:
                allowed = db.object_accessible(tenant_id=t,digest=digest)
            finally:
                db.close()
            if not allowed:
                raise HTTPException(status_code=404, detail="Object not found")
            store = object_store_factory()
            data = store.get(digest)
            return Response(content=data, media_type="application/octet-stream",
                            headers={"X-Content-SHA256": digest})
        except HTTPException:
            raise
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
        "/v1/observations/{device_id}/aggregate", tags=["Observations"],
        summary="Aggregate observations over an event-time range",
    )
    def aggregate_observations(device_id: str, property: str,
                               start_at: str, end_at: str,
                               t: str = Depends(auth)):
        try:
            db=store_factory()
            try:
                result=db.aggregate_observations(
                    tenant_id=t,device_id=device_id,property=property,
                    start_at=aware_iso(start_at),end_at=aware_iso(end_at))
            finally:
                db.close()
            return {"tenant_id":t,"device_id":device_id,"property":property,
                    "aggregate":result}
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc))

    @app.get(
        "/v1/observations/{device_id}", tags=["Observations"],
        summary="Query observations for a device",
    )
    def get_observations(device_id: str, property: str, limit: int = 100,
                         start_at: str | None = None, end_at: str | None = None,
                         t: str = Depends(auth)):
        try:
            db = store_factory()
            try:
                items = db.observations(tenant_id=t, device_id=device_id,
                                        property=property, limit=limit,
                                        start_at=aware_iso(start_at),
                                        end_at=aware_iso(end_at))
            finally:
                db.close()
            return {"tenant_id": t, "device_id": device_id,
                    "property": property, "items": items}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get(
        "/v1/memories/{memory_id}/sources", tags=["Memory"],
        summary="List provenance sources linked to a memory",
    )
    def get_memory_sources(memory_id: str, t: str = Depends(auth)):
        memory_id = require_uuid(memory_id)
        try:
            db=store_factory()
            try:
                items=db.sources_for_memory(tenant_id=t,memory_id=memory_id)
            finally:
                db.close()
            return {"memory_id":memory_id,"sources":items}
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc))

    @app.put(
        "/v1/memories/{memory_id}/embedding", tags=["Retrieval"],
        summary="Attach a caller-generated embedding to a memory",
    )
    def put_embedding(memory_id: str, item: EmbeddingWrite,
                      t: str = Depends(auth)):
        memory_id = require_uuid(memory_id)
        if not (capability_flags or {}).get("semantic", False):
            raise HTTPException(status_code=503, detail="Vector indexing is disabled on this instance")
        try:
            db = store_factory()
            try:
                db.store_embedding(tenant_id=t,memory_id=memory_id,
                                   model_id=item.model_id,vector=item.embedding)
            finally:
                db.close()
            return {"memory_id":memory_id,"model_id":item.model_id,
                    "dimensions":len(item.embedding),"indexed":True}
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.post(
        "/v1/entities", status_code=201, tags=["Knowledge Graph"],
        summary="Create or update a canonical entity",
    )
    def put_entity(item: EntityWrite, t: str = Depends(auth)):
        try:
            db=store_factory()
            try:
                db.upsert_entity(tenant_id=t,entity_id=item.entity_id,
                    entity_type=item.entity_type,canonical_name=item.canonical_name,
                    aliases=item.aliases,attributes=item.attributes)
            finally:
                db.close()
            return {"entity_id":item.entity_id,"canonical_name":item.canonical_name}
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc))

    @app.get(
        "/v1/entities/resolve", tags=["Knowledge Graph"],
        summary="Resolve a canonical entity name or alias",
    )
    def resolve_entity(name: str, t: str = Depends(auth)):
        try:
            db=store_factory()
            try:
                entity=db.resolve_entity(tenant_id=t,name=name)
            finally:
                db.close()
            if entity is None:
                raise HTTPException(status_code=404,detail="Entity not found")
            return entity
        except ValueError as exc:
            raise HTTPException(status_code=409,detail=str(exc))

    @app.post(
        "/v1/edges", status_code=201, tags=["Knowledge Graph"],
        summary="Create a canonical entity relationship and optionally project it to AGE",
    )
    def put_edge(item: EdgeWrite, t: str = Depends(auth)):
        try:
            db=store_factory()
            try:
                edge_id=db.add_edge(tenant_id=t,from_entity=item.from_entity,
                    predicate=item.predicate,to_entity=item.to_entity,
                    source_memory_id=item.source_memory_id)
                projected=0
                if (capability_flags or {}).get("graph",False):
                    from .age_graph import AgeProjection
                    graph=AgeProjection(db.conn)
                    graph.initialize()
                    projected=graph.project_pending(tenant_id=t).projected
            finally:
                db.close()
            return {"edge_id":edge_id,"projected_to_age":projected}
        except (ValueError,PermissionError) as exc:
            raise HTTPException(status_code=422,detail=str(exc))

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
                    yield "event: error\ndata: " + json.dumps({"detail":str(exc)}) + "\n\n"
                    return
                if items:
                    for item in items:
                        last_id = item["event_id"]
                        payload = {"event_id":last_id,"event_type":item["event_type"],
                                   "payload":item["payload"],"recorded_at":str(item["recorded_at"])}
                        yield "id: " + last_id + "\nevent: " + item["event_type"] + "\ndata: " + json.dumps(payload,default=str) + "\n\n"
                else:
                    yield ": keepalive\n\n"
                    await asyncio.sleep(0.5)
        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

    @app.post(
        "/v1/webhooks", status_code=201, tags=["Events"],
        summary="Create a signed, durable webhook subscription",
    )
    def create_webhook(item: WebhookCreate, t: str = Depends(auth)):
        import hashlib, hmac
        from .webhooks import WebhookStore, validate_target
        master=webhook_signing_key or os.getenv("MEMORY_WEBHOOK_SIGNING_KEY")
        allowed=webhook_allowed_hosts
        if allowed is None:
            allowed={x.strip().lower() for x in os.getenv("MEMORY_WEBHOOK_ALLOWED_HOSTS","").split(",") if x.strip()}
        if not master or len(master)<32:
            raise HTTPException(status_code=503,detail="Webhook signing key is not configured")
        try:
            target=validate_target(item.target_url,set(allowed))
            db=store_factory()
            try:
                store=WebhookStore(db.conn)
                subscription_id=store.create(tenant_id=t,target_url=target,event_types=item.event_types)
            finally:
                db.close()
            secret=hmac.new(master.encode(),("agent-memory-webhook:"+subscription_id).encode(),hashlib.sha256).hexdigest()
            return {"subscription_id":subscription_id,"active":True,
                    "signing_secret":secret,"signature_header":"X-Memory-Signature",
                    "signature_format":"sha256=HMAC-SHA256(secret, timestamp + '.' + raw_request_body)",
                    "timestamp_header":"X-Memory-Timestamp",
                    "delivery_header":"X-Memory-Delivery"}
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc))

    @app.get("/v1/webhooks",tags=["Events"],summary="List this tenant's webhook subscriptions")
    def list_webhooks(t: str = Depends(auth)):
        from .webhooks import WebhookStore
        db=store_factory()
        try:
            return {"items":WebhookStore(db.conn).list(tenant_id=t)}
        finally:
            db.close()

    @app.get("/v1/webhooks/{subscription_id}/deliveries",tags=["Events"],
             summary="Inspect delivery attempts for a tenant-owned webhook")
    def webhook_deliveries(subscription_id: str,limit: int=100,t: str = Depends(auth)):
        from .webhooks import WebhookStore
        try:
            subscription_id=str(UUID(subscription_id))
            db=store_factory()
            try:
                items=WebhookStore(db.conn).deliveries(tenant_id=t,subscription_id=subscription_id,limit=limit)
            finally:
                db.close()
            return {"items":items}
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc))

    @app.post("/v1/webhooks/deliveries/{delivery_id}/replay",tags=["Events"],
              summary="Requeue a dead webhook delivery belonging to this tenant")
    def replay_webhook_delivery(delivery_id: str,t: str = Depends(auth)):
        from .webhooks import WebhookStore
        try:
            delivery_id=str(UUID(delivery_id))
            db=store_factory()
            try:
                replayed=WebhookStore(db.conn).requeue_dead(tenant_id=t,delivery_id=delivery_id)
            finally:
                db.close()
            if not replayed:
                raise HTTPException(status_code=404,detail="Dead webhook delivery not found")
            return {"delivery_id":delivery_id,"requeued":True}
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc))

    @app.delete("/v1/webhooks/{subscription_id}",tags=["Events"],
                summary="Revoke this tenant's webhook subscription")
    def revoke_webhook(subscription_id: str,t: str = Depends(auth)):
        from .webhooks import WebhookStore
        try:
            db=store_factory()
            try:
                revoked=WebhookStore(db.conn).revoke(tenant_id=t,subscription_id=subscription_id)
            finally:
                db.close()
            if not revoked:
                raise HTTPException(status_code=404,detail="Webhook subscription not found")
            return {"subscription_id":subscription_id,"revoked":True}
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc))

    @app.post("/v1/events/outbox/{event_id}/replay",tags=["Events"],
              summary="Replay a dead transactional outbox event for this tenant")
    def replay_outbox_event(event_id: str,t: str = Depends(auth)):
        from .outbox import OutboxWorker
        try:
            event_id=str(UUID(event_id))
            db=store_factory()
            try:
                replayed=OutboxWorker(db.conn).requeue_dead(
                    tenant_id=t,event_id=event_id)
            finally:
                db.close()
            if not replayed:
                raise HTTPException(status_code=404,detail="Dead outbox event not found")
            return {"event_id":event_id,"requeued":True}
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc))

    @app.post(
        "/v1/memories/process", status_code=200, tags=["Memory"],
        summary="Process extracted facts into canonical memories",
        description=("Runs deterministic validation, confidence filtering, exact-fact deduplication, "
                     "relation classification (DUPLICATE / SUPERSEDES / CONTRADICTION / MERGED / CREATED), "
                     "optional consolidation when a candidate sets merge=true, provenance linking, "
                     "entity alias resolution and bitemporal fact replacement. "
                     "The caller supplies structured candidates from its own local model/rules extractor; "
                     "this endpoint does not pretend to extract facts from arbitrary text by itself."),
    )
    def process_memories(item: ProcessRequest, t: str = Depends(auth)):
        from .processing import MemoryProcessor
        db = store_factory()
        try:
            if item.candidates is not None:
                extractor = lambda _text: item.candidates
            elif memory_extractor_factory is not None:
                extractor = memory_extractor_factory(t)
                if not callable(extractor):
                    raise RuntimeError("Configured local memory extractor is unavailable")
            else:
                raise HTTPException(
                    status_code=503,
                    detail="No local memory extractor configured; provide candidates or configure memory_extractor_factory",
                )
            processor = MemoryProcessor(
                db,
                confidence_threshold=item.confidence_threshold,
                entity_resolver=lambda tenant_id, name: db.resolve_entity(
                    tenant_id=tenant_id, name=name),
                embedder=embedder if (embedder is not None and auto_embed) else None,
            )
            report = processor.process(
                tenant_id=t, text=item.text, source_id=item.source_id,
                extractor=extractor,
            )
            return {
                "tenant_id": t,
                "created": report.created,
                "duplicates": report.duplicates,
                "rejected": report.rejected,
                "replaced": report.replaced,
                "relations": report.relations,
                "embedding_failures": report.embedding_failures,
            }
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="Local memory extractor unavailable or invalid") from exc
        finally:
            db.close()

    @app.post(
        "/v1/workflows/memory-verifications", status_code=202, tags=["Workflows"],
        summary="Start a durable Temporal memory verification workflow",
        description="Starts the registered VerifyMemoryWorkflow. Requires a separately running Temporal Worker on the configured task queue.",
        responses={503: {"description": "Temporal client is not configured or unavailable."}},
    )
    async def start_memory_verification(item: MemoryVerificationRequest,
                                        t: str = Depends(auth)):
        if temporal_client_factory is None or not temporal_task_queue:
            raise HTTPException(status_code=503,detail="Temporal workflow runtime is not configured")
        try:
            memory_id=str(UUID(item.memory_id))
            client=await temporal_client_factory()
            workflow_id=t+":"+(item.workflow_id or "memory-verify-"+str(uuid4()))
            handle=await client.start_workflow(
                "VerifyMemoryWorkflow",
                {"tenant_id":t,"memory_id":memory_id},
                id=workflow_id,task_queue=temporal_task_queue,
            )
            return {"workflow_id":handle.id,"run_id":getattr(handle,"result_run_id",None),
                    "workflow_type":"VerifyMemoryWorkflow","status":"STARTED"}
        except ValueError as exc:
            raise HTTPException(status_code=422,detail="memory_id must be a valid UUID")
        except Exception:
            raise HTTPException(status_code=503,detail="Temporal workflow start failed")

    @app.get(
        "/v1/workflows/{workflow_id}", tags=["Workflows"],
        summary="Describe a Temporal workflow execution",
    )
    async def describe_workflow(workflow_id: str,t: str = Depends(auth)):
        if not workflow_id.startswith(t+":"):
            raise HTTPException(status_code=404,detail="Temporal workflow execution not found")
        if temporal_client_factory is None or not temporal_task_queue:
            raise HTTPException(status_code=503,detail="Temporal workflow runtime is not configured")
        try:
            client=await temporal_client_factory()
            description=await client.get_workflow_handle(workflow_id).describe()
            status=getattr(description.status,"name",str(description.status))
            return {"workflow_id":workflow_id,"run_id":description.run_id,
                    "status":status,"workflow_type":str(getattr(description,"workflow_type","VerifyMemoryWorkflow"))}
        except (KeyError, LookupError):
            raise HTTPException(status_code=404,detail="Temporal workflow execution not found")
        except Exception:
            raise HTTPException(status_code=503,detail="Temporal workflow service is unavailable")

    @app.post(
        "/v1/workflows/{workflow_id}/cancel", tags=["Workflows"],
        summary="Request cancellation of a Temporal workflow",
    )
    async def cancel_workflow(workflow_id: str,t: str = Depends(auth)):
        if not workflow_id.startswith(t+":"):
            raise HTTPException(status_code=404,detail="Temporal workflow execution not found")
        if temporal_client_factory is None or not temporal_task_queue:
            raise HTTPException(status_code=503,detail="Temporal workflow runtime is not configured")
        try:
            client=await temporal_client_factory()
            await client.get_workflow_handle(workflow_id).cancel()
            return {"workflow_id":workflow_id,"cancel_requested":True}
        except (KeyError, LookupError):
            raise HTTPException(status_code=404,detail="Temporal workflow execution not found")
        except Exception:
            raise HTTPException(status_code=503,detail="Temporal workflow service is unavailable")

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
            query_embedding = item.embedding
            query_model_id = item.model_id
            if (query_embedding is None and embedder is not None and auto_embed
                    and flags.get("semantic", False)):
                try:
                    query_embedding = embedder.embed_text(item.query)
                    query_model_id = embedder.model_id
                except Exception:
                    # Provider outage degrades to lexical channels instead of
                    # failing the whole search.
                    query_embedding = None
                    query_model_id = None
            service = search_factory()
            try:
                report = service.search_detailed(
                    tenant_id=t, query=item.query, embedding=query_embedding,
                    model_id=query_model_id, anchor_entity=item.anchor_entity,
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
