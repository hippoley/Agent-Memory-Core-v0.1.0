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
