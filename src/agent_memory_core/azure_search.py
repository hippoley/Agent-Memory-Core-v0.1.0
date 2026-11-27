"""Azure AI Search is an optional candidate index, NOT an Azure Blob vector bucket.

An injected SearchClient supports offline tests. Cloud candidates are rechecked
against AMC's canonical PostgreSQL tenant, time, and supersession constraints.
Index schema must be provisioned separately; never auto-create on query.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit

from .vector_bucket import VectorBucketAdapter, VectorCandidate, _safe_identity, _vector


_INDEX = re.compile(r"^[a-z0-9][a-z0-9-]{1,126}[a-z0-9]$")
_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class AzureSearchConfig:
    endpoint: str
    index: str
    dimensions: int
    vector_field: str = "embedding"

    def __post_init__(self) -> None:
        url = urlsplit(self.endpoint)
        if (url.scheme != "https" or not re.fullmatch(
                r"[a-z0-9-]+\.search\.windows\.net", url.hostname or "")
                or url.username or url.password or url.port
                or url.path not in ("", "/") or url.query or url.fragment):
            raise ValueError("expected Azure AI Search service HTTPS endpoint")
        if not _INDEX.fullmatch(self.index) or not _FIELD.fullmatch(self.vector_field):
            raise ValueError("invalid Azure AI Search index or vector field")
        if not 1 <= self.dimensions <= 4096:
            raise ValueError("invalid vector dimensions")


class AzureSearchAdapter:
    """Implements AMC's put/query/delete candidate API using Azure SearchClient.

    Required index schema:
      id: Edm.String key/retrievable
      tenant_id: Edm.String filterable/retrievable
      memory_id: Edm.String retrievable
      model_id: Edm.String filterable/retrievable
      embedding: Collection(Edm.Single), searchable, dimensions=config.dimensions,
        vectorSearchProfile=<preprovisioned profile>
    """

    def __init__(self, config: AzureSearchConfig, *, client: Any,
                 vector_query_factory: Callable[..., Any] | None = None):
        self.config = config
        self.client = client
        self.vector_query_factory = vector_query_factory

    @classmethod
    def from_azure_sdk(cls, config: AzureSearchConfig, *, credential: Any):
        from azure.search.documents import SearchClient
        return cls(config, client=SearchClient(
            endpoint=config.endpoint, index_name=config.index, credential=credential))

    @staticmethod
    def _check_results(results: Any) -> None:
        for result in results:
            if not getattr(result, "succeeded", False):
                raise RuntimeError("Azure AI Search document indexing failed")

    def put(self, *, tenant_id: str, memory_id: str, model_id: str,
            embedding: Sequence[float]) -> str:
        key = VectorBucketAdapter.key_for(tenant_id, memory_id, model_id)
        vector = _vector(embedding, self.config.dimensions)
        results = self.client.upload_documents(documents=[{
            "id": key, "tenant_id": tenant_id, "memory_id": memory_id,
            "model_id": model_id, self.config.vector_field: vector,
        }])
        self._check_results(results)
        return key

    def query(self, *, tenant_id: str, model_id: str,
              embedding: Sequence[float], top_k: int = 10) -> list[VectorCandidate]:
        _safe_identity(tenant_id, "tenant")
        _safe_identity(model_id, "model")
        if not 1 <= top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")
        vector = _vector(embedding, self.config.dimensions)
        if self.vector_query_factory is None:
            from azure.search.documents.models import VectorizedQuery
            query = VectorizedQuery(vector=vector, k_nearest_neighbors=top_k,
                                    fields=self.config.vector_field)
        else:
            query = self.vector_query_factory(
                vector=vector, k_nearest_neighbors=top_k, fields=self.config.vector_field)
        def literal(value: str) -> str:
            return value.replace("'", "''")
        odata_filter = (f"tenant_id eq '{literal(tenant_id)}' and "
                        f"model_id eq '{literal(model_id)}'")
        rows = self.client.search(
            search_text=None, vector_queries=[query],
            filter=odata_filter, vector_filter_mode="preFilter",
            select=["id", "tenant_id", "model_id", "memory_id"], top=top_k)
        candidates: list[VectorCandidate] = []
        seen: set[str] = set()
        for row in rows:
            memory_id = row.get("memory_id")
            key = row.get("id")
            if (row.get("tenant_id") != tenant_id or row.get("model_id") != model_id
                    or not isinstance(memory_id, str) or not isinstance(key, str)
                    or memory_id in seen
                    or key != VectorBucketAdapter.key_for(tenant_id, memory_id, model_id)):
                continue
            # Azure's @search.score is a relevance score, not S3 cosine distance.
            # Intentionally discard rather than mislabel it as a distance.
            seen.add(memory_id)
            candidates.append(VectorCandidate(memory_id=memory_id, distance=None, key=key))
        return candidates

    def delete(self, *, tenant_id: str, memory_id: str, model_id: str) -> None:
        key = VectorBucketAdapter.key_for(tenant_id, memory_id, model_id)
        self._check_results(self.client.delete_documents(documents=[{"id": key}]))
