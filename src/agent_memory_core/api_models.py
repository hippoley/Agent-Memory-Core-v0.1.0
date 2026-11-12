"""Public response schemas for generated Swagger/OpenAPI 3.1.

Schemas describe the real HTTP contract; optional fields accommodate adapters
that return reduced projections. Do not infer that an optional backend exists.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field, ConfigDict


class HealthResponse(BaseModel):
    status: str = Field(examples=["alive"])
    contract_version: str = Field(examples=["1.0.0"])


class ReadinessResponse(BaseModel):
    status: str = Field(examples=["ready"])
    checks: dict[str, str]
    contract_version: str


class CapabilityItem(BaseModel):
    name: str
    available: bool
    reason: str = ""


class CapabilitiesResponse(BaseModel):
    contract_version: str
    tenant_id: str
    capabilities: list[CapabilityItem]


class MemoryWriteResponse(BaseModel):
    id: str = Field(description="UUID of newly inserted canonical claim.")
    tenant_id: str = Field(description="Tenant taken from server authentication, not the request body.")
    embedded: bool | None = Field(
        default=None,
        description=("Present only when a server-side embedding provider is configured: "
                     "true when the claim was vectorised on write, false when the provider "
                     "failed and the vector can be retried by a projection worker."))


class MemoryItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    subject_id: str | None = None
    predicate: str | None = None
    value: dict[str, Any] | None = None
    source_id: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    recorded_at: datetime | None = None
    superseded_at: datetime | None = None
    object_sha256: str | None = None


class MemoryReadResponse(BaseModel):
    contract_version: str
    items: list[MemoryItem]
    # Present only on the browse endpoint (GET /v1/memories).
    count: int | None = None
    limit: int | None = None
    offset: int | None = None


class SearchHit(BaseModel):
    memory_id: str
    subject_id: str
    predicate: str
    value: dict[str, Any]
    source_id: str
    score: float
    channels: list[str]


class SearchResponse(BaseModel):
    contract_version: str
    items: list[SearchHit]
    channels_used: list[str]
    degraded_channels: dict[str, str]
