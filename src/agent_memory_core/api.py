"""Local-only REST gateway. Server-side tenant credential is mandatory.

This is not production-ready authentication: no TLS, RLS, rate limits, identity
federation or fine-grained authorization. Bind only to loopback.
"""
from __future__ import annotations
from datetime import datetime
import os
import hmac
from .contracts import CONTRACT_VERSION,capabilities,aware_iso
from pydantic import BaseModel, Field, ConfigDict
from fastapi import Header

class Claim(BaseModel):
    model_config=ConfigDict(extra="forbid")
    subject_id: str=Field(min_length=1,max_length=255)
    predicate: str=Field(min_length=1,max_length=255)
    value: dict
    source_id: str=Field(min_length=1,max_length=255)
    valid_from: str
    valid_to: str|None=None
    replaces_id: str|None=None
    object_sha256: str|None=None


def create_app(*, api_key=None, tenant_id=None, store_factory=None, search_factory=None, capability_flags=None):
    from fastapi import FastAPI, HTTPException, Depends

    key=api_key if api_key is not None else os.getenv("MEMORY_API_KEY")
    tenant=tenant_id if tenant_id is not None else os.getenv("MEMORY_TENANT_ID")
    if not key or len(key)<24 or not tenant:
        raise RuntimeError("MEMORY_API_KEY (24+ chars) and MEMORY_TENANT_ID required")
    if store_factory is None:
        def store_factory():
            from .postgres import PostgresMemory
            return PostgresMemory(os.environ["MEMORY_DSN"])
    app=FastAPI(title="Agent Memory Core",version=CONTRACT_VERSION,
                docs_url=None,redoc_url=None,openapi_url=None)

    def auth(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401,detail="Bearer token required")
        if not hmac.compare_digest(authorization[7:],key):
            raise HTTPException(status_code=403,detail="Invalid credential")
        return tenant


    class SearchRequest(BaseModel):
        model_config=ConfigDict(extra="forbid")
        query: str=Field(min_length=1,max_length=2000)
        embedding: list[float]|None=None
        model_id: str|None=None
        anchor_entity: str|None=None
        limit: int=Field(default=10,ge=1,le=100)
        valid_at: str|None=None
        known_at: str|None=None

    @app.get("/v1/health")
    def health():
        return {"status":"alive","contract_version":CONTRACT_VERSION}

    @app.get("/v1/capabilities")
    def get_capabilities(t: str=Depends(auth)):
        return {"contract_version":CONTRACT_VERSION,"tenant_id":t,
                "capabilities":capabilities(**(capability_flags or {}))}

    @app.post("/v1/memories",status_code=201)
    def add_claim(item: Claim,t: str=Depends(auth)):
        try:
            valid_from=aware_iso(item.valid_from)
            valid_to=aware_iso(item.valid_to)
            db=store_factory()
            try:
                mid=db.remember(tenant_id=t,subject_id=item.subject_id,
                    predicate=item.predicate,value=item.value,source_id=item.source_id,
                    valid_from=valid_from,valid_to=valid_to,
                    replaces_id=item.replaces_id,object_sha256=item.object_sha256)
            finally: db.close()
            return {"id":mid,"tenant_id":t}
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc))

    @app.get("/v1/memories/{subject_id}")
    def recall(subject_id: str,predicate: str|None=None,
               valid_at: str|None=None,known_at: str|None=None,
               t: str=Depends(auth)):
        try:
            va=aware_iso(valid_at)
            ka=aware_iso(known_at)
            db=store_factory()
            try:
                return {"contract_version":CONTRACT_VERSION,
                        "items":db.recall(tenant_id=t,subject_id=subject_id,
                            predicate=predicate,valid_at=va,known_at=ka)}
            finally: db.close()
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc))
    @app.post("/v1/search")
    def search_memories(item: SearchRequest,t: str=Depends(auth)):
        if search_factory is None:
            raise HTTPException(status_code=503,detail="Hybrid search adapter is not configured")
        try:
            service=search_factory()
            try:
                report=service.search_detailed(tenant_id=t,query=item.query,
                    embedding=item.embedding,model_id=item.model_id,
                    anchor_entity=item.anchor_entity,limit=item.limit,
                    valid_at=aware_iso(item.valid_at),known_at=aware_iso(item.known_at))
                return {"contract_version":CONTRACT_VERSION,
                        "items":[{"memory_id":h.memory_id,"subject_id":h.subject_id,
                                  "predicate":h.predicate,"value":h.value,
                                  "source_id":h.source_id,"score":h.score,
                                  "channels":list(h.channels)} for h in report.hits],
                        "channels_used":list(report.channels_used),
                        "degraded_channels":report.degraded_channels}
            finally:
                close=getattr(service,"close",None)
                if callable(close): close()
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc))

    return app
