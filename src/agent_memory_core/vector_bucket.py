"""Optional AWS S3 Vectors / Alibaba OSS Vectors candidate-index adapter.

This is NOT a canonical memory store. The caller MUST re-fetch candidate
memory IDs from PostgreSQL with tenant/temporal/authorization checks.
No credentials, endpoints or raw claim contents are logged or persisted here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from typing import Any, Sequence

_SAFE_INDEX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


@dataclass(frozen=True)
class VectorBucketConfig:
    provider: str
    bucket: str
    index: str
    region: str
    dimensions: int
    endpoint_url: str | None = None

    def __post_init__(self) -> None:
        if self.provider not in {"aws", "aliyun"}:
            raise ValueError("provider must be aws or aliyun")
        if not self.bucket or not self.index or not _SAFE_INDEX.fullmatch(self.index):
            raise ValueError("valid vector bucket and index are required")
        if not self.region or not 1 <= self.dimensions <= 4096:
            raise ValueError("region and supported dimensions are required")
        if self.provider == "aliyun":
            if not self.endpoint_url or not self.endpoint_url.startswith("https://"):
                raise ValueError("aliyun requires explicit HTTPS vector bucket endpoint")
        elif self.endpoint_url:
            raise ValueError("AWS custom endpoints are disabled by this adapter")


@dataclass(frozen=True)
class VectorCandidate:
    memory_id: str
    distance: float | None
    key: str


def _vector(values: Sequence[float], dimension: int) -> list[float]:
    if len(values) != dimension:
        raise ValueError("vector dimension mismatch")
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in values):
        raise ValueError("vector must contain finite numeric values")
    return [float(x) for x in values]


def _safe_identity(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(f"invalid {label}")
    return value


class VectorBucketAdapter:
    """Inject a boto3 s3vectors client for offline tests, or construct it explicitly.

    Access policy must be tenant-scoped by the application; metadata filtering
    alone is NOT a security boundary. Prefer separate indexes where required.
    """

    def __init__(self, config: VectorBucketConfig, *, client: Any):
        self.config = config
        self.client = client

    @classmethod
    def from_boto3(cls, config: VectorBucketConfig, *,
                   aws_access_key_id: str | None = None,
                   aws_secret_access_key: str | None = None,
                   aws_session_token: str | None = None):
        import boto3
        args: dict[str, Any] = {"region_name": config.region}
        if config.provider == "aliyun":
            if not aws_access_key_id or not aws_secret_access_key:
                raise ValueError("aliyun requires explicitly supplied RAM credentials")
            args["endpoint_url"] = config.endpoint_url
        if aws_access_key_id is not None:
            args["aws_access_key_id"] = aws_access_key_id
        if aws_secret_access_key is not None:
            args["aws_secret_access_key"] = aws_secret_access_key
        if aws_session_token is not None:
            args["aws_session_token"] = aws_session_token
        return cls(config, client=boto3.client("s3vectors", **args))

    @staticmethod
    def key_for(tenant_id: str, memory_id: str, model_id: str) -> str:
        # Hash a length-prefixed tuple so delimiters cannot cause collisions.
        pieces = [_safe_identity(x, name).encode("utf-8")
                  for x, name in ((tenant_id, "tenant"), (memory_id, "memory"), (model_id, "model"))]
        data = b"".join(len(x).to_bytes(4, "big") + x for x in pieces)
        return "amc-v1-" + hashlib.sha256(data).hexdigest()

    def put(self, *, tenant_id: str, memory_id: str, model_id: str,
            embedding: Sequence[float]) -> str:
        vector = _vector(embedding, self.config.dimensions)
        key = self.key_for(tenant_id, memory_id, model_id)
        self.client.put_vectors(
            vectorBucketName=self.config.bucket, indexName=self.config.index,
            vectors=[{"key": key, "data": {"float32": vector},
                      "metadata": {"tenant_id": tenant_id, "memory_id": memory_id,
                                   "model_id": model_id}}],
        )
        return key

    def query(self, *, tenant_id: str, model_id: str,
              embedding: Sequence[float], top_k: int = 10) -> list[VectorCandidate]:
        _safe_identity(tenant_id, "tenant")
        _safe_identity(model_id, "model")
        if not 1 <= top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")
        vector = _vector(embedding, self.config.dimensions)
        response = self.client.query_vectors(
            vectorBucketName=self.config.bucket, indexName=self.config.index,
            queryVector={"float32": vector}, topK=top_k,
            filter={"$and": [{"tenant_id": tenant_id}, {"model_id": model_id}]},
            returnMetadata=True, returnDistance=True,
        )
        results: list[VectorCandidate] = []
        seen: set[str] = set()
        for row in response.get("vectors", []):
            metadata = row.get("metadata") or {}
            memory_id = metadata.get("memory_id")
            key = row.get("key")
            if (metadata.get("tenant_id") != tenant_id
                    or metadata.get("model_id") != model_id
                    or not isinstance(memory_id, str)
                    or not isinstance(key, str)
                    or key != self.key_for(tenant_id, memory_id, model_id)
                    or memory_id in seen):
                continue  # Never accept a cross-tenant or mismatched provider response.
            distance = row.get("distance")
            if distance is not None and (not isinstance(distance, (int, float))
                                         or not math.isfinite(distance)):
                continue
            seen.add(memory_id)
            results.append(VectorCandidate(memory_id, float(distance) if distance is not None else None, key))
        return results

    def delete(self, *, tenant_id: str, memory_id: str, model_id: str) -> None:
        self.client.delete_vectors(
            vectorBucketName=self.config.bucket, indexName=self.config.index,
            keys=[self.key_for(tenant_id, memory_id, model_id)],
        )
