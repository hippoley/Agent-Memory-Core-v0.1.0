"""Local-first REST gateway. Bind to loopback unless deployed behind a hardened gateway."""
from __future__ import annotations

import hashlib
import hashlib
import hmac
import os
import asyncio
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .contracts import CONTRACT_VERSION, aware_iso, capabilities
from .api_models import (HealthResponse, ReadinessResponse, CapabilitiesResponse, MemoryWriteResponse,
                         MemoryReadResponse, SearchResponse)
from .api_docs import SERVICE_DESCRIPTION, TAG_METADATA, install as install_api_docs


def _subject_resolver(db):
    """Resolve a literal subject name, registering it on first sight.

    A text extractor emits a name ("my car", "the charity run"), not an entity
    id. Silently dropping every fact about a subject nobody pre-registered made
    free-text ingestion useless — the caller had to know the ontology before it
    could remember anything. Names are therefore registered as generic
    ``concept`` entities with a deterministic id, so concurrent writers collide
    on the same row instead of creating duplicates. Explicit entities and
    aliases still win: resolution runs first and is never overridden.
    """
    upsert = getattr(db, "upsert_entity", None)

    def resolve(tenant_id: str, name: str):
        found = db.resolve_entity(tenant_id=tenant_id, name=name)
        if found is not None or not callable(upsert):
            return found
        cleaned = " ".join(str(name).split())[:200]
        if not cleaned:
            return None
        digest = hashlib.sha256(cleaned.casefold().encode("utf-8")).hexdigest()[:8]
        slug = re.sub(r"[^a-z0-9]+", "-", cleaned.casefold()).strip("-")[:40]
        entity_id = f"auto-{slug}-{digest}" if slug else f"auto-{digest}"
        upsert(tenant_id=tenant_id, entity_id=entity_id, entity_type="concept",
               canonical_name=cleaned, aliases=[], attributes={"auto_registered": True})
        return db.resolve_entity(tenant_id=tenant_id, name=cleaned)

    return resolve


def _subject_resolver(db):
    """Resolve a literal subject name, registering it on first sight.

    A text extractor emits a name ("my car", "the charity run"), not an entity
    id. Silently dropping every fact about a subject nobody pre-registered made
    free-text ingestion useless — the caller had to know the ontology before it
    could remember anything. Names are therefore registered as generic
    ``concept`` entities with a deterministic id, so concurrent writers collide
    on the same row instead of creating duplicates. Explicit entities and
    aliases still win: resolution runs first and is never overridden.
    """
    upsert = getattr(db, "upsert_entity", None)

    def resolve(tenant_id: str, name: str):
        found = db.resolve_entity(tenant_id=tenant_id, name=name)
        if found is not None or not callable(upsert):
            return found
        cleaned = " ".join(str(name).split())[:200]
        if not cleaned:
            return None
        digest = hashlib.sha256(cleaned.casefold().encode("utf-8")).hexdigest()[:8]
        slug = re.sub(r"[^a-z0-9]+", "-", cleaned.casefold()).strip("-")[:40]
        entity_id = f"auto-{slug}-{digest}" if slug else f"auto-{digest}"
        upsert(tenant_id=tenant_id, entity_id=entity_id, entity_type="concept",
               canonical_name=cleaned, aliases=[], attributes={"auto_registered": True})
        return db.resolve_entity(tenant_id=tenant_id, name=cleaned)

    return resolve


_SWAGGER_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Swagger UI · Agent Memory Core</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css" />
<style>body{margin:0;font-family:-apple-system,"PingFang SC",system-ui,sans-serif}
#fallback{display:none;max-width:640px;margin:80px auto;padding:0 24px;font-size:14px;line-height:1.8;color:#3b352c}
#fallback code{background:#f3ede0;padding:2px 6px;border-radius:4px}
</style></head>
<body>
<div id="swagger-ui"></div>
<div id="fallback"><h2>Swagger UI 需要访问 CDN</h2>
<p>当前网络无法加载 <code>swagger-ui-dist</code>。这不影响接口本身：</p>
<p>· 使用内置离线手册 <a href="/docs/manual">/docs/manual</a>（含每个接口的 User Story 与 Try it）<br />
· 或直接读取机器可读的 <a href="/openapi.json">/openapi.json</a></p></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
if (window.SwaggerUIBundle) {
  window.ui = SwaggerUIBundle({url:"/openapi.json",dom_id:"#swagger-ui",
    deepLinking:true,docExpansion:"list",defaultModelsExpandDepth:0,
    persistAuthorization:true,tryItOutEnabled:true});
} else { document.getElementById("fallback").style.display = "block"; }
</script>
</body></html>
"""


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
    predicate: str = Field(min_length=1, max_length=255, description="Fact type chosen by the caller, e.g. preference, account_status, or device_state.")
    value: dict = Field(description="Structured JSON fact payload.")
    source_id: str = Field(min_length=1, max_length=255, description="Provenance identifier, e.g. user utterance, sensor event, or document.")
    valid_from: str = Field(description="Timezone-aware ISO-8601 timestamp; inclusive real-world validity start.")
    valid_to: str | None = Field(default=None, description="Timezone-aware ISO-8601 exclusive validity end.")
    replaces_id: str | None = Field(default=None, description="Existing claim UUID to correct; subject and predicate must match.")
    object_sha256: str | None = Field(default=None, description="Optional SHA-256 reference to separately stored evidence object.")


class PredicatePolicyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cardinality: str = Field(
        pattern="^(one|many)$",
        description=("one = at most one active claim per subject+predicate "
                     "(e.g. current_status); many = distinct claims coexist "
                     "(e.g. contact_phone). This is trusted tenant policy: a model "
                     "or extractor cannot raise or lower it."))


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

    atomic_batch: bool = Field(
        default=False,
        description=(
            "Opt-in all-or-nothing PostgreSQL transaction for 1..500 canonical candidates. "
            "Every candidate must have a stable subject_id and predicate. A rejected "
            "candidate or unmatched RETRACT aborts the entire batch, including events "
            "and outbox; the default non-atomic ingestion behavior is unchanged. "
            "Embeddings are best-effort projections after canonical commit."
        ),
    )
    context_turns: list[str] = Field(default_factory=list, max_length=3, description="Optional recent prior utterances in chronological order (each <=2000 chars). Only explicitly configured contextual extractors use them; unsupported providers fail closed.")
    ground_with_memory: bool = Field(default=False, description="Opt in to retrieving tenant-scoped canonical memory as evidence for LLM revision planning; requires a supporting model extractor and a search adapter.")
    confidence_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    observed_at: str | None = Field(
        default=None,
        description=("Optional timezone-aware ISO-8601 instant at which this text was originally "
                     "observed, for replaying historical conversations. It becomes both the "
                     "knowledge time (recorded_at) and the effective-from time of the claims, so "
                     "later time-travel queries see the same order the user experienced. Omit it "
                     "for live ingestion; future instants are rejected."))


class MemoryVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_id: str = Field(min_length=36, max_length=36, description="Canonical memory claim UUID to verify.")
    workflow_id: str | None = Field(default=None, min_length=1, max_length=255, description="Optional stable Temporal workflow ID; generated if omitted.")


class DurableTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_type: str = Field(min_length=1, max_length=120)
    input: dict[str, Any]
    workflow_id: str | None = Field(default=None, max_length=255)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)


class DurableCheckpointWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str = Field(min_length=1, max_length=255)
    lease_epoch: int = Field(ge=1)
    expected_version: int = Field(ge=0)
    checkpoint: dict[str, Any]
    idempotency_key: str = Field(min_length=1, max_length=255)
    progress_completed: int = Field(default=0, ge=0)
    progress_total: int = Field(default=0, ge=0)
    artifact_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    effect_key: str | None = Field(default=None, min_length=1, max_length=255)
    effect_request: dict[str, Any] | None = None


class DurableTaskStatusWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str = Field(min_length=1, max_length=255)
    lease_epoch: int = Field(ge=1)
    status: str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class DurableTaskClaimWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str = Field(min_length=1, max_length=255)
    lease_seconds: int = Field(default=30, ge=1, le=3600)


class DurableTaskRenewWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str = Field(min_length=1, max_length=255)
    lease_epoch: int = Field(ge=1)
    lease_seconds: int = Field(default=30, ge=1, le=3600)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)
    embedding: list[float] | None = Field(default=None, description="Caller-generated query embedding. Supported dimensions depend on configured model profile.")
    model_id: str | None = Field(default=None, description="Required when embedding is supplied.")
    anchor_entity: str | None = Field(default=None, description="Optional graph entity identifier for AGE neighborhood expansion.")
    limit: int = Field(default=10, ge=1, le=100)
    valid_at: str | None = Field(default=None, description="Timezone-aware ISO-8601 world-time filter.")
    known_at: str | None = Field(default=None, description="Timezone-aware ISO-8601 knowledge-time filter.")


class TenantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=120)


class TenantSelect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str = Field(min_length=1, max_length=255)


class CredentialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1, max_length=8)
    expires_in_days: int | None = Field(default=90, ge=1, le=365)


def create_app(*, api_key=None, tenant_id=None, store_factory=None,
               search_factory=None, object_store_factory=None, capability_flags=None,
               temporal_client_factory=None, temporal_task_queue=None, memory_extractor_factory=None,
               embedder=None, auto_embed=True,
               webhook_signing_key=None, webhook_allowed_hosts=None,
               identity_store_factory=None):
    """Create a single-tenant app. Tenant identity is bound server-side to the API key.

    ``embedder`` is an optional provider-backed object exposing ``model_id``,
    ``embed_text(text) -> vector`` and ``__call__(candidate) -> (model_id, vector)``.
    When present and ``auto_embed`` is true, claims are vectorised on write and
    queries are vectorised on search, so the semantic channel works without the
    caller computing vectors. Provider failure never fails the canonical write.
    """
    key = api_key if api_key is not None else os.getenv("MEMORY_API_KEY")
    tenant = tenant_id if tenant_id is not None else os.getenv("MEMORY_TENANT_ID")
    legacy_auth_enabled = bool(key and len(key) >= 24 and tenant)
    if not legacy_auth_enabled and identity_store_factory is None:
        raise RuntimeError(
            "Configure either MEMORY_API_KEY (24+ chars) + MEMORY_TENANT_ID "
            "or an identity_store_factory")

    if store_factory is None:
        def store_factory():
            from .postgres import PostgresMemory
            return PostgresMemory(os.environ["MEMORY_DSN"])

    app = FastAPI(
        title="Agent Memory Core API",
        version=CONTRACT_VERSION,
        description=SERVICE_DESCRIPTION,
        # The built-in pages depend on a CDN; this service is deployed on
        # isolated networks, so /docs is served from packaged static assets
        # and the stock Swagger UI stays available at /docs/swagger.
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json",
        openapi_tags=TAG_METADATA,
    )
    bearer = HTTPBearer(
        auto_error=False,
        description="API key issued by the service operator. Send: Authorization: Bearer <key>.",
    )

    def auth(request: Request,
             credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        presented = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else None
        if legacy_auth_enabled and presented and hmac.compare_digest(presented, key):
            request.state.identity = {"auth_type": "legacy", "tenant_id": tenant}
            return tenant

        if identity_store_factory is not None:
            selected_tenant = request.headers.get("X-Memory-Tenant")
            session_cookie = request.cookies.get("amc_session")
            csrf_cookie = request.cookies.get("amc_csrf")
            csrf_header = request.headers.get("X-CSRF-Token")
            token = session_cookie or presented
            if not token or not selected_tenant:
                raise HTTPException(
                    status_code=401,
                    detail="Session or API credential and X-Memory-Tenant are required",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if session_cookie and request.method not in ("GET", "HEAD", "OPTIONS"):
                if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
                    raise HTTPException(status_code=403, detail="CSRF validation failed")
            identity_store = identity_store_factory()
            try:
                identity = identity_store.authenticate_api_credential(secret=token) if presented and not session_cookie else None
                auth_type = "api_credential" if identity is not None else "session"
                if identity is None:
                    identity = identity_store.authenticate_session(
                        token=token, tenant_id=selected_tenant)
                if identity is None or identity.get("tenant_id") != selected_tenant:
                    raise HTTPException(status_code=401, detail="Invalid or expired identity")
                method = request.method.upper()
                safe_method = method in ("GET", "HEAD", "OPTIONS")
                role = identity.get("role")
                if role not in ("owner", "admin", "editor", "reader"):
                    raise HTTPException(status_code=403, detail="Membership role is invalid")
                if not safe_method and role == "reader":
                    raise HTTPException(status_code=403, detail="Reader membership is read-only")
                scopes = identity.get("scopes")
                if scopes is not None:
                    worker_mutation = (
                        request.method.upper() == "POST"
                        and request.url.path.startswith("/v1/tasks/")
                    )
                    required_scope = (
                        "tasks:execute" if worker_mutation
                        else ("memory:read" if safe_method else "memory:write")
                    )
                    if required_scope not in scopes and "memory:*" not in scopes:
                        raise HTTPException(status_code=403, detail="Credential scope is insufficient")
                identity["auth_type"] = auth_type
                request.state.identity = identity
                return selected_tenant
            finally:
                identity_store.close()

        if presented is None:
            raise HTTPException(
                status_code=401, detail="Bearer token required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        raise HTTPException(status_code=403, detail="Invalid credential")

    def task_worker_auth(request: Request, t: str = Depends(auth)):
        identity = getattr(request.state, "identity", None) or {}
        scopes = identity.get("scopes") or []
        if identity.get("auth_type") != "api_credential":
            raise HTTPException(status_code=403, detail="Dedicated task worker credential required")
        if "tasks:execute" not in scopes and "memory:*" not in scopes:
            raise HTTPException(status_code=403, detail="tasks:execute scope required")
        return t

    @app.get("/v1/auth/oidc/login", tags=["Identity"], summary="Start OIDC browser login")
    def oidc_login():
        from .oidc import OIDCClient, OIDCConfig
        try:
            config = OIDCConfig.from_env()
            authorization_url, transaction_cookie = OIDCClient(config).begin()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="OIDC login is not configured") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail="OIDC provider discovery failed") from exc
        response = RedirectResponse(authorization_url, status_code=302)
        response.set_cookie(
            "amc_oidc_tx", transaction_cookie, max_age=600, path="/v1/auth/oidc",
            httponly=True, secure=config.cookie_secure, samesite="lax")
        return response

    @app.get("/v1/auth/oidc/callback", tags=["Identity"], summary="Complete OIDC browser login")
    def oidc_callback(request: Request, code: str | None = None, state: str | None = None):
        from .oidc import OIDCClient, OIDCConfig
        if not code or not state or not request.cookies.get("amc_oidc_tx"):
            raise HTTPException(status_code=400, detail="OIDC callback is incomplete")
        try:
            config = OIDCConfig.from_env()
            client = OIDCClient(config)
            transaction = client.verify_transaction(request.cookies["amc_oidc_tx"], state)
            claims = client.exchange_and_verify(code=code, transaction=transaction)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="OIDC login is not configured") from exc
        except Exception as exc:
            raise HTTPException(status_code=401, detail="OIDC authentication failed") from exc

        identity_store = identity_store_factory() if identity_store_factory else None
        if identity_store is None:
            raise HTTPException(status_code=503, detail="Identity control plane is unavailable")
        try:
            login = identity_store.complete_oidc_login(
                issuer=config.issuer, subject=claims["sub"],
                email=claims.get("email") if isinstance(claims.get("email"), str) else None,
                email_verified=claims.get("email_verified") is True,
                display_name=(claims.get("name") if isinstance(claims.get("name"), str) else
                              claims.get("preferred_username") if isinstance(claims.get("preferred_username"), str) else None),
                allow_signup=config.allow_signup, bootstrap_workspace=True,
                user_agent=request.headers.get("user-agent"))
        except LookupError as exc:
            raise HTTPException(status_code=403, detail="Account is not provisioned for this identity") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="Account is not active") from exc
        finally:
            identity_store.close()

        csrf_token = secrets.token_urlsafe(24)
        response = RedirectResponse("/dashboard/observatory.html", status_code=303)
        response.delete_cookie("amc_oidc_tx", path="/v1/auth/oidc",
                               secure=config.cookie_secure, samesite="lax")
        response.set_cookie(
            "amc_session", login["token"], max_age=43200, path="/",
            httponly=True, secure=config.cookie_secure, samesite="lax")
        response.set_cookie(
            "amc_csrf", csrf_token, max_age=43200, path="/",
            httponly=False, secure=config.cookie_secure, samesite="lax")
        response.set_cookie(
            "amc_tenant", login["tenants"][0]["tenant_id"], max_age=43200, path="/",
            httponly=False, secure=config.cookie_secure, samesite="lax")
        return response

    @app.get("/v1/identity/tenants", tags=["Identity"], summary="List active tenant memberships")
    def identity_tenants(request: Request,
                         credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        token = request.cookies.get("amc_session")
        if not token and credentials and credentials.scheme.lower() == "bearer":
            token = credentials.credentials
        if not token or identity_store_factory is None:
            raise HTTPException(status_code=401, detail="Identity session or API credential required")
        identity_store = identity_store_factory()
        try:
            tenants = identity_store.list_session_tenants(token=token)
            if not tenants:
                credential = identity_store.authenticate_api_credential(secret=token)
                if credential is not None:
                    tenants = [{
                        "tenant_id": credential["tenant_id"],
                        "display_name": credential["tenant_id"],
                        "role": credential["role"],
                        "membership_version": credential["membership_version"],
                    }]
            if not tenants:
                raise HTTPException(status_code=401, detail="Invalid or expired identity")
            return {"items": tenants, "count": len(tenants)}
        finally:
            identity_store.close()

    @app.post("/v1/identity/tenants", status_code=201, tags=["Identity"],
              summary="Create a workspace owned by the current principal")
    def identity_create_tenant(item: TenantCreate, request: Request, response: Response,
                               t: str = Depends(auth)):
        identity = getattr(request.state, "identity", None) or {}
        principal_id = identity.get("principal_id")
        if identity.get("auth_type") != "session" or not principal_id:
            raise HTTPException(status_code=403, detail="Workspace creation requires a browser session")
        display_name = item.display_name.strip()
        if not display_name:
            raise HTTPException(status_code=422, detail="Workspace display name cannot be blank")
        tenant_id = "ws-" + uuid4().hex
        identity_store = identity_store_factory()
        try:
            identity_store.create_tenant(
                tenant_id=tenant_id, display_name=display_name,
                owner_principal_id=principal_id)
        finally:
            identity_store.close()
        secure_cookie = os.getenv("MEMORY_COOKIE_SECURE", "1") == "1"
        response.set_cookie("amc_tenant", tenant_id, max_age=43200, path="/",
                            httponly=False, secure=secure_cookie, samesite="lax")
        return {"tenant_id": tenant_id, "display_name": display_name,
                "role": "owner", "created": True}

    @app.post("/v1/identity/select-tenant", tags=["Identity"],
              summary="Switch the current browser session workspace")
    def identity_select_tenant(item: TenantSelect, request: Request, response: Response,
                               t: str = Depends(auth)):
        identity = getattr(request.state, "identity", None) or {}
        token = request.cookies.get("amc_session")
        if identity.get("auth_type") != "session" or not token:
            raise HTTPException(status_code=403, detail="Workspace switching requires a browser session")
        identity_store = identity_store_factory()
        try:
            memberships = identity_store.list_session_tenants(token=token)
            selected = next((row for row in memberships
                             if row.get("tenant_id") == item.tenant_id), None)
            if selected is None:
                raise HTTPException(status_code=403, detail="Principal is not an active member of this workspace")
        finally:
            identity_store.close()
        secure_cookie = os.getenv("MEMORY_COOKIE_SECURE", "1") == "1"
        response.set_cookie("amc_tenant", item.tenant_id, max_age=43200, path="/",
                            httponly=False, secure=secure_cookie, samesite="lax")
        return {"tenant_id": item.tenant_id, "display_name": selected.get("display_name"),
                "role": selected.get("role"), "selected": True}

    @app.post("/v1/identity/api-credentials", status_code=201, tags=["Identity"],
              summary="Create a tenant-scoped API credential for SDK integration")
    def identity_create_api_credential(item: CredentialCreate, request: Request,
                                       t: str = Depends(auth)):
        # API keys must not mint replacement credentials. Explicit human
        # sessions own the bootstrap step and receive the secret exactly once.
        identity = getattr(request.state, "identity", None) or {}
        if identity.get("auth_type") != "session" or identity.get("role") not in ("owner", "admin"):
            raise HTTPException(status_code=403, detail="Owner/Admin browser session required")
        if not item.name.strip():
            raise HTTPException(status_code=422, detail="Credential name cannot be blank")
        allowed = {"memory:read", "memory:write", "memory:*", "tasks:execute"}
        if any(scope not in allowed for scope in item.scopes) or len(set(item.scopes)) != len(item.scopes):
            raise HTTPException(status_code=422, detail="Invalid or duplicate credential scope")
        from datetime import timedelta
        store = identity_store_factory()
        try:
            issued = store.issue_api_credential(
                principal_id=identity["principal_id"], tenant_id=t,
                name=item.name.strip(), scopes=item.scopes,
                ttl=timedelta(days=item.expires_in_days) if item.expires_in_days else None,
            )
            return {**issued, "tenant_id": t, "name": item.name.strip(),
                    "scopes": item.scopes, "expires_in_days": item.expires_in_days}
        finally:
            store.close()

    @app.delete("/v1/identity/api-credentials/{credential_id}", tags=["Identity"],
                summary="Revoke one of your API credentials immediately")
    def identity_revoke_api_credential(credential_id: str, request: Request,
                                      t: str = Depends(auth)):
        identity = getattr(request.state, "identity", None) or {}
        if identity.get("auth_type") != "session":
            raise HTTPException(status_code=403, detail="Browser session required")
        cid = require_uuid(credential_id)
        store = identity_store_factory()
        try:
            revoked = store.revoke_api_credential(
                credential_id=cid, principal_id=identity["principal_id"])
            if not revoked:
                raise HTTPException(status_code=404, detail="Credential not found")
            return {"credential_id": cid, "revoked": True}
        finally:
            store.close()

    @app.get("/v1/identity/me", tags=["Identity"], summary="Get the authenticated principal and tenant membership")
    def identity_me(request: Request, t: str = Depends(auth)):
        identity = getattr(request.state, "identity", None) or {}
        if identity.get("auth_type") == "legacy":
            return {"tenant_id": t, "auth_type": "legacy_bearer"}
        return {
            "principal_id": identity.get("principal_id"),
            "tenant_id": t,
            "role": identity.get("role"),
            "membership_version": identity.get("membership_version"),
            "auth_type": identity.get("auth_type"),
        }

    @app.post("/v1/auth/logout", tags=["Identity"], summary="Revoke the current session")
    def identity_logout(request: Request, response: Response,
                        t: str = Depends(auth)):
        identity = getattr(request.state, "identity", None) or {}
        if identity.get("auth_type") == "session" and identity_store_factory is not None:
            identity_store = identity_store_factory()
            try:
                identity_store.revoke_session(
                    session_id=identity["session_id"],
                    principal_id=identity["principal_id"])
            finally:
                identity_store.close()
        secure_cookie = os.getenv("MEMORY_COOKIE_SECURE", "1") == "1"
        response.delete_cookie("amc_session", path="/", httponly=True,
                                secure=secure_cookie, samesite="lax")
        response.delete_cookie("amc_csrf", path="/", secure=secure_cookie, samesite="lax")
        response.delete_cookie("amc_tenant", path="/", secure=secure_cookie, samesite="lax")
        response.delete_cookie("amc_oidc_tx", path="/v1/auth/oidc", httponly=True,
                                secure=secure_cookie, samesite="lax")
        return {"logged_out": True}

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

    @app.get(
        "/v1/evaluation/search-config", tags=["System"],
        summary="Attest effective search aliases for isolated evaluation",
        description=(
            "Authenticated, read-only inspection of the live search adapter. "
            "Evaluation runners must verify this before comparing alias ablations. "
            "A search implementation that cannot expose its effective alias mapping "
            "returns 409 rather than inventing a disabled configuration."
        ),
    )
    def get_evaluation_search_config(t: str = Depends(auth)):
        if search_factory is None:
            raise HTTPException(status_code=503, detail="Search adapter is unavailable")
        service = search_factory()
        try:
            mapping = getattr(service, "query_aliases", None)
            if not isinstance(mapping, dict) or not all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in mapping.items()
            ):
                raise HTTPException(
                    status_code=409, detail="Search adapter cannot attest effective aliases"
                )
            encoded = json.dumps(
                mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            return {
                "tenant_id": t,
                "aliases_enabled": bool(mapping),
                "alias_count": len(mapping),
                "alias_sha256": hashlib.sha256(encoded).hexdigest(),
            }
        finally:
            close = getattr(service, "close", None)
            if callable(close):
                close()

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
        "/v1/predicate-policies", tags=["Memory"],
        summary="List trusted predicate cardinality policies",
        description=(
            "Cardinality is tenant-scoped trusted configuration stored in "
            "memory_predicate_policies. Unlisted predicates fall back to the "
            "instance default (MEMORY_DEFAULT_CARDINALITY, 'one' unless the operator "
            "chose 'many'), so a room cannot hold two current temperatures unless an "
            "operator explicitly declares that predicate multi-valued. Models and "
            "callers cannot change it."
        ),
    )
    def list_predicate_policies(t: str = Depends(auth)):
        db = store_factory()
        try:
            items = db.list_predicate_policies(tenant_id=t)
        finally:
            db.close()
        return {"tenant_id": t,
                "default": getattr(db, "default_cardinality", "one"),
                "items": items}

    @app.put(
        "/v1/predicate-policies/{predicate}", tags=["Memory"],
        summary="Set trusted predicate cardinality",
        description=(
            "Operator action, not model output. Changing a predicate that already has "
            "claims is refused (409) until those claims are reconciled, so history can "
            "never be silently reinterpreted."
        ),
    )
    def set_predicate_policy(predicate: str, item: PredicatePolicyWrite,
                             t: str = Depends(auth)):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", predicate):
            raise HTTPException(
                status_code=422,
                detail="predicate must be short snake_case (a-z, 0-9, _)")
        try:
            db = store_factory()
            try:
                db.set_predicate_cardinality(
                    tenant_id=t, predicate=predicate,
                    cardinality=item.cardinality)
            finally:
                db.close()
        except ValueError as exc:
            message = str(exc)
            status = 409 if "reconciliation" in message else 422
            raise HTTPException(status_code=status, detail=message)
        return {"tenant_id": t, "predicate": predicate,
                "cardinality": item.cardinality, "updated": True}

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
            if any(not isinstance(turn, str) or not turn.strip() or len(turn) > 2000
                   for turn in item.context_turns):
                raise HTTPException(status_code=422, detail="context_turns must contain nonempty text of at most 2000 characters each")
            if item.ground_with_memory and item.observed_at is not None:
                # Historical backfills require a historical evidence snapshot;
                # current-state retrieval must never rewrite history by mistake.
                raise HTTPException(status_code=422, detail="Evidence grounding supports live revisions only, not historical replay")
            provider_extractor = None
            if item.candidates is not None:
                if item.context_turns or item.ground_with_memory:
                    raise HTTPException(status_code=422, detail="Cannot mix candidate facts with server-side context or grounding")
                extractor = lambda _text: item.candidates
            elif memory_extractor_factory is not None:
                provider_extractor = memory_extractor_factory(t)
                if not callable(provider_extractor):
                    raise RuntimeError("Configured local memory extractor is unavailable")
                if item.ground_with_memory:
                    from .evidence import evidence_from_hits, guard_retractions
                    grounded = getattr(provider_extractor, "extract_with_evidence", None)
                    if not callable(grounded):
                        raise HTTPException(status_code=422, detail="Extractor does not support evidence-grounded revision")
                    if search_factory is None:
                        raise HTTPException(status_code=503, detail="Grounding requires a search adapter")
                    query_embedding = None
                    query_model = None
                    if embedder is not None and auto_embed and (capability_flags or {}).get("semantic", False):
                        try:
                            query_embedding = embedder.embed_text(item.text)
                            query_model = embedder.model_id
                        except Exception:
                            # Semantic provider unavailable: canonical SQL/FTS
                            # may still retrieve evidence. No fabricated hits.
                            pass
                    search = search_factory()
                    try:
                        report = search.search_detailed(
                            tenant_id=t, query=item.text,
                            embedding=query_embedding, model_id=query_model,
                            limit=8,
                        )
                        evidence = evidence_from_hits(report.hits)
                    finally:
                        close = getattr(search, "close", None)
                        if callable(close):
                            close()
                    def extractor(text):
                        proposed = grounded(text, item.context_turns, evidence)
                        return guard_retractions(proposed, evidence)
                elif item.context_turns:
                    contextual = getattr(provider_extractor, "extract_with_context", None)
                    if not callable(contextual):
                        raise HTTPException(status_code=422, detail="Configured extractor does not support explicit context resolution")
                    extractor = lambda text: contextual(text, item.context_turns)
                else:
                    extractor = provider_extractor
            else:
                raise HTTPException(
                    status_code=503,
                    detail="No local memory extractor configured; provide candidates or configure memory_extractor_factory",
                )
            processor = MemoryProcessor(
                db,
                confidence_threshold=item.confidence_threshold,
                entity_resolver=_subject_resolver(db),
                # External model/vector calls must not hold SQL locks. In strict
                # batches, embed only AFTER the canonical transaction commits.
                embedder=embedder if (embedder is not None and auto_embed
                                      and not item.atomic_batch) else None,
            )
            observed_at = None
            if item.observed_at is not None:
                try:
                    observed_at = datetime.fromisoformat(
                        item.observed_at.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise HTTPException(
                        status_code=422,
                        detail="observed_at must be a timezone-aware ISO-8601 timestamp") from exc
                if observed_at.tzinfo is None:
                    raise HTTPException(
                        status_code=422,
                        detail="observed_at must carry a timezone offset")
                if observed_at > datetime.now(timezone.utc):
                    raise HTTPException(
                        status_code=422,
                        detail="observed_at cannot be in the future")
            if item.atomic_batch:
                if observed_at is not None:
                    # The current explicit-withdrawal path records knowledge
                    # time at mutation, not a historical snapshot. Reject
                    # backdated compound operations until the full bitemporal
                    # transaction semantics are implemented.
                    raise HTTPException(
                        status_code=422,
                        detail="Atomic batch does not support historical observed_at",
                    )
                # Materialize model output exactly once outside the transaction.
                # Reject unbounded outputs before acquiring any SQL locks.
                from itertools import islice
                atomic_context = getattr(db, "atomic_candidate_batch", None)
                if not callable(atomic_context):
                    raise HTTPException(
                        status_code=503,
                        detail="Atomic batch requires the PostgreSQL canonical store",
                    )
                candidates = list(islice(iter(extractor(item.text)), 501))
                if not 1 <= len(candidates) <= 500:
                    raise HTTPException(
                        status_code=422, detail="Atomic batch requires 1..500 candidates",
                    )
                with atomic_context(tenant_id=t, candidates=candidates):
                    report = processor.process(
                        tenant_id=t, text=item.text, source_id=item.source_id,
                        extractor=lambda _text: candidates, observed_at=observed_at,
                    )
                    # The ordinary processor deliberately supports partial
                    # acceptance. Strict mode must turn that into a rollback,
                    # including old-claim withdrawals and their durable outbox.
                    if (report.rejected or
                            any(r.get("relation") == "NOOP" for r in report.relations)):
                        raise HTTPException(
                            status_code=409,
                            detail="Atomic batch rolled back: rejected candidate or unmet RETRACT precondition",
                        )
                # Semantic vectors are projections, not canonical claims.
                # Never invoke an external embedding model while holding a
                # multi-key database lock. Report best-effort failures honestly.
                if embedder is not None and auto_embed:
                    for candidate, relation in zip(candidates, report.relations):
                        if relation.get("relation") in ("DUPLICATE", "RETRACTED", "NOOP"):
                            continue
                        memory_id = relation["memory_id"]
                        try:
                            model_id, vector = embedder(candidate)
                            db.store_embedding(
                                tenant_id=t, memory_id=memory_id,
                                model_id=model_id, vector=vector,
                            )
                        except Exception:
                            report.embedding_failures.append(memory_id)
            else:
                report = processor.process(
                    tenant_id=t, text=item.text, source_id=item.source_id,
                    extractor=extractor, observed_at=observed_at,
                )
            result = {
                "tenant_id": t,
                "created": report.created,
                "duplicates": report.duplicates,
                "rejected": report.rejected,
                "replaced": report.replaced,
                "relations": report.relations,
                "embedding_failures": report.embedding_failures,
            }
            if item.atomic_batch:
                result["atomic_batch"] = True
            if provider_extractor is not None:
                snapshot = getattr(provider_extractor, "attestation_snapshot", None)
                if callable(snapshot):
                    result["model_call_attestation"] = snapshot()
            return result
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
                                        request: Request,
                                        t: str = Depends(auth)):
        if temporal_client_factory is None or not temporal_task_queue:
            raise HTTPException(status_code=503,detail="Temporal workflow runtime is not configured")
        try:
            memory_id=str(UUID(item.memory_id))
            client=await temporal_client_factory()
            workflow_id=t+":"+(item.workflow_id or "memory-verify-"+str(uuid4()))
            identity = getattr(request.state, "identity", None) or {}
            if identity.get("auth_type") == "legacy":
                if os.getenv("MEMORY_TEMPORAL_REQUIRE_RLS", "0") == "1":
                    raise HTTPException(status_code=403, detail="Legacy credentials cannot enqueue revocable identity-bound work")
            workflow_request = {
                "tenant_id": t,
                "memory_id": memory_id,
                "principal_id": identity.get("principal_id"),
                "session_id": identity.get("session_id"),
                "credential_id": identity.get("credential_id"),
                "membership_version": identity.get("membership_version"),
            }
            handle=await client.start_workflow(
                "VerifyMemoryWorkflow",
                workflow_request,
                id=workflow_id,task_queue=temporal_task_queue,
            )
            return {"workflow_id":handle.id,"run_id":getattr(handle,"result_run_id",None),
                    "workflow_type":"VerifyMemoryWorkflow","status":"STARTED"}
        except HTTPException:
            raise
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

    @app.get(
        "/v1/workflows/{workflow_id}/progress", tags=["Workflows"],
        summary="Read durable workflow progress",
    )
    async def workflow_progress(workflow_id: str,t: str = Depends(auth)):
        if not workflow_id.startswith(t+":"):
            raise HTTPException(status_code=404,detail="Temporal workflow execution not found")
        if temporal_client_factory is None or not temporal_task_queue:
            raise HTTPException(status_code=503,detail="Temporal workflow runtime is not configured")
        try:
            client=await temporal_client_factory()
            progress=await client.get_workflow_handle(workflow_id).query("progress")
            return {"workflow_id":workflow_id,**progress}
        except (KeyError, LookupError):
            raise HTTPException(status_code=404,detail="Temporal workflow execution not found")
        except Exception:
            raise HTTPException(status_code=503,detail="Temporal workflow progress is unavailable")

    @app.post(
        "/v1/workflows/{workflow_id}/pause", tags=["Workflows"],
        summary="Pause a workflow at its next safe stage boundary",
    )
    async def pause_workflow(workflow_id: str,t: str = Depends(auth)):
        if not workflow_id.startswith(t+":"):
            raise HTTPException(status_code=404,detail="Temporal workflow execution not found")
        if temporal_client_factory is None or not temporal_task_queue:
            raise HTTPException(status_code=503,detail="Temporal workflow runtime is not configured")
        try:
            client=await temporal_client_factory()
            await client.get_workflow_handle(workflow_id).signal("pause")
            return {"workflow_id":workflow_id,"pause_requested":True,
                    "semantics":"takes effect at the next safe stage boundary"}
        except (KeyError, LookupError):
            raise HTTPException(status_code=404,detail="Temporal workflow execution not found")
        except Exception:
            raise HTTPException(status_code=503,detail="Temporal workflow pause is unavailable")

    @app.post(
        "/v1/workflows/{workflow_id}/resume", tags=["Workflows"],
        summary="Resume a paused workflow",
    )
    async def resume_workflow(workflow_id: str,t: str = Depends(auth)):
        if not workflow_id.startswith(t+":"):
            raise HTTPException(status_code=404,detail="Temporal workflow execution not found")
        if temporal_client_factory is None or not temporal_task_queue:
            raise HTTPException(status_code=503,detail="Temporal workflow runtime is not configured")
        try:
            client=await temporal_client_factory()
            await client.get_workflow_handle(workflow_id).signal("resume")
            return {"workflow_id":workflow_id,"resume_requested":True}
        except (KeyError, LookupError):
            raise HTTPException(status_code=404,detail="Temporal workflow execution not found")
        except Exception:
            raise HTTPException(status_code=503,detail="Temporal workflow resume is unavailable")

    @app.post("/v1/tasks", status_code=201, tags=["Durable Tasks"],
                  summary="Create an idempotent durable business task")
    def create_durable_task(item: DurableTaskCreate, request: Request,
                            t: str = Depends(auth)):
        from .durable_tasks import DurableTaskStore, TaskConflict
        db=store_factory()
        try:
            identity=getattr(request.state,"identity",None) or {}
            tasks=DurableTaskStore(db.conn)
            return tasks.create_task(
                tenant_id=t,task_type=item.task_type,input=item.input,
                created_by_principal_id=identity.get("principal_id"),
                workflow_id=item.workflow_id,idempotency_key=item.idempotency_key,
            )
        except TaskConflict as exc:
            raise HTTPException(status_code=409,detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc))
        finally:
            db.close()

    @app.get("/v1/tasks", tags=["Durable Tasks"],
             summary="List durable tasks in the authenticated tenant")
    def list_durable_tasks(limit: int = 50, offset: int = 0,
                           t: str = Depends(auth)):
        from .durable_tasks import DurableTaskStore
        db=store_factory()
        try:
            return {"items":DurableTaskStore(db.conn).list_tasks(
                tenant_id=t,limit=limit,offset=offset)}
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc))
        finally:
            db.close()

    @app.get("/v1/tasks/{task_id}", tags=["Durable Tasks"],
             summary="Read a durable task and its latest checkpoint")
    def get_durable_task(task_id: str, t: str = Depends(auth)):
        from .durable_tasks import DurableTaskStore
        try:
            task_id=str(UUID(task_id))
        except ValueError:
            raise HTTPException(status_code=422,detail="task_id must be a UUID")
        db=store_factory()
        try:
            task=DurableTaskStore(db.conn).get_task(tenant_id=t,task_id=task_id)
            if task is None:
                raise HTTPException(status_code=404,detail="Task not found")
            return task
        finally:
            db.close()

    @app.post("/v1/tasks/worker/claim", tags=["Durable Tasks"],
              summary="Atomically claim one task using a dedicated worker credential")
    def claim_durable_task(item: DurableTaskClaimWrite,
                           t: str = Depends(task_worker_auth)):
        from .worker_leases import DurableTaskLeaseStore
        db=store_factory()
        try:
            task=DurableTaskLeaseStore(db.conn).claim(
                tenant_id=t,worker_id=item.worker_id,lease_seconds=item.lease_seconds)
            return {"task":task}
        finally:
            db.close()

    @app.post("/v1/tasks/{task_id}/lease/renew", tags=["Durable Tasks"],
              summary="Renew a task lease using its current fencing epoch")
    def renew_durable_task_lease(task_id: str, item: DurableTaskRenewWrite,
                                 t: str = Depends(task_worker_auth)):
        from .worker_leases import DurableTaskLeaseStore
        try:
            task_id=str(UUID(task_id))
        except ValueError:
            raise HTTPException(status_code=422,detail="task_id must be a UUID")
        db=store_factory()
        try:
            renewed=DurableTaskLeaseStore(db.conn).renew(
                tenant_id=t,task_id=task_id,worker_id=item.worker_id,
                lease_epoch=item.lease_epoch,lease_seconds=item.lease_seconds)
            if not renewed:
                raise HTTPException(status_code=409,detail="Lease expired or worker was fenced")
            return {"task_id":task_id,"renewed":True,"lease_epoch":item.lease_epoch}
        finally:
            db.close()

    @app.post("/v1/tasks/{task_id}/checkpoints", status_code=201,
              tags=["Durable Tasks"],
              summary="Commit a checkpoint only with the current worker lease and fencing epoch")
    def commit_durable_checkpoint(task_id: str, item: DurableCheckpointWrite,
                                   t: str = Depends(task_worker_auth)):
        from .worker_leases import DurableTaskLeaseStore, LeaseConflict
        try:
            task_id=str(UUID(task_id))
        except ValueError:
            raise HTTPException(status_code=422,detail="task_id must be a UUID")
        db=store_factory()
        try:
            return DurableTaskLeaseStore(db.conn).commit_checkpoint(
                tenant_id=t,task_id=task_id,worker_id=item.worker_id,
                lease_epoch=item.lease_epoch,expected_version=item.expected_version,
                checkpoint=item.checkpoint,idempotency_key=item.idempotency_key,
                progress_completed=item.progress_completed,
                progress_total=item.progress_total,
                artifact_sha256=item.artifact_sha256,
                effect_key=item.effect_key,effect_request=item.effect_request,
            )
        except LookupError:
            raise HTTPException(status_code=404,detail="Task not found")
        except LeaseConflict as exc:
            raise HTTPException(status_code=409,detail=str(exc))
        except Exception as exc:
            from .durable_tasks import TaskConflict
            if isinstance(exc,TaskConflict):
                raise HTTPException(status_code=409,detail=str(exc))
            raise
        finally:
            db.close()

    @app.post("/v1/tasks/{task_id}/status", tags=["Durable Tasks"],
              summary="Finish or pause a task using the current worker lease")
    def update_durable_task_status(task_id: str, item: DurableTaskStatusWrite,
                                   t: str = Depends(task_worker_auth)):
        from .worker_leases import DurableTaskLeaseStore, LeaseConflict
        try:
            task_id=str(UUID(task_id))
        except ValueError:
            raise HTTPException(status_code=422,detail="task_id must be a UUID")
        db=store_factory()
        try:
            DurableTaskLeaseStore(db.conn).finish(
                tenant_id=t,task_id=task_id,worker_id=item.worker_id,
                lease_epoch=item.lease_epoch,status=item.status,
                result=item.result,error=item.error)
            return {"task_id":task_id,"status":item.status}
        except LeaseConflict as exc:
            raise HTTPException(status_code=409,detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc))
        finally:
            db.close()

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
                            "valid_from": (getattr(h, "valid_from", None).isoformat()
                                           if getattr(h, "valid_from", None) else None),
                            "recorded_at": (getattr(h, "recorded_at", None).isoformat()
                                            if getattr(h, "recorded_at", None) else None),
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

    docs_dir = Path(__file__).resolve().parent / "resources" / "docs"
    if docs_dir.is_dir():
        # Self-contained manual: no CDN, works on an air-gapped demo network.
        # Every operation renders its user story from the x-user-story field
        # that install_api_docs() injects into /openapi.json.
        # /docs is the stock Swagger UI (the entry people expect);
        # the CDN-free manual lives at /docs/manual and /redoc points there,
        # so an air-gapped instance still has readable API docs.
        @app.get("/docs", include_in_schema=False)
        def swagger_ui():
            return Response(_SWAGGER_PAGE, media_type="text/html; charset=utf-8")

        @app.get("/docs/swagger", include_in_schema=False)
        def swagger_ui_alias():
            return Response(_SWAGGER_PAGE, media_type="text/html; charset=utf-8")

        @app.get("/docs/manual", include_in_schema=False)
        def api_manual():
            return Response(
                (docs_dir / "api.html").read_text(encoding="utf-8"),
                media_type="text/html; charset=utf-8")

        @app.get("/docs/quickstart", include_in_schema=False)
        def api_quickstart():
            return Response(
                (docs_dir / "quickstart.html").read_text(encoding="utf-8"),
                media_type="text/html; charset=utf-8")

        @app.get("/redoc", include_in_schema=False)
        def redoc_redirect():
            return RedirectResponse("/docs/manual")

    install_api_docs(app)

    dashboard_dir = Path(__file__).resolve().parent / "resources" / "dashboard"
    if dashboard_dir.is_dir():
        # Same-origin static assets: the console talks to /v1/* without CORS,
        # and the bearer key never leaves the browser except in that header.
        app.mount("/dashboard", StaticFiles(directory=str(dashboard_dir), html=True),
                  name="dashboard")

        @app.get("/", include_in_schema=False)
        def dashboard_redirect():
            return RedirectResponse("/dashboard/")

    return app
