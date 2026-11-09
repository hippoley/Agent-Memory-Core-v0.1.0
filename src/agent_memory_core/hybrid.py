"""Hybrid retrieval with explicit optional-channel degradation.

SQL is the required canonical channel. Vector and graph are optional indexes:
their failures are surfaced in diagnostics while healthy channels continue.
No index result bypasses canonical tenant/time revalidation.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone

@dataclass(frozen=True)
class MemoryHit:
    memory_id: str
    subject_id: str
    predicate: str
    value: dict
    source_id: str
    score: float
    channels: tuple[str, ...]

@dataclass(frozen=True)
class SearchReport:
    hits: tuple[MemoryHit, ...]
    channels_used: tuple[str, ...]
    degraded_channels: dict[str, str]

class HybridMemorySearch:
    def __init__(self, store, graph=None):
        self.store = store
        self.graph = graph

    def close(self) -> None:
        close = getattr(self.store, "close", None)
        if callable(close):
            close()

    def search(self, **kwargs) -> list[MemoryHit]:
        return list(self.search_detailed(**kwargs).hits)

    def search_detailed(self, *, tenant_id: str, query: str,
               embedding: list[float] | None = None, model_id: str | None = None,
               anchor_entity: str | None = None, limit: int = 10,
               valid_at: datetime | None = None, known_at: datetime | None = None) -> SearchReport:
        if not tenant_id or not (1 <= limit <= 100):
            raise ValueError("Invalid tenant or limit")
        if embedding is not None and not model_id:
            raise ValueError("model_id required with embedding")
        valid_at = valid_at or datetime.now(timezone.utc)
        known_at = known_at or datetime.now(timezone.utc)
        ranked: dict[str, list[tuple[str, int]]] = {}
        degraded: dict[str, str] = {}
        k = 60

        if embedding is not None:
            try:
                vector_rows=self.store.vector_search(
                    tenant_id=tenant_id,model_id=model_id,vector=embedding,
                    limit=min(limit*10,100),valid_at=valid_at,known_at=known_at)
                for rank,(mid,_) in enumerate(vector_rows,1):
                    ranked.setdefault(mid,[]).append(("vector",rank))
            except Exception as exc:
                degraded["vector"]=f"{type(exc).__name__}: {str(exc)[:240]}"

        if query and query.strip():
            # POSITION treats the user query literally; no LIKE wildcard or
            # ESCAPE dialect ambiguity across PostgreSQL versions.
            rows = self.store.conn.execute(
                """SELECT id::text FROM memory_claims
                   WHERE tenant_id=%s
                     AND position(lower(%s) in lower(value::text)) > 0
                     AND recorded_at<=%s AND (superseded_at IS NULL OR superseded_at>%s)
                     AND valid_from<=%s AND (valid_to IS NULL OR valid_to>%s)
                   ORDER BY recorded_at DESC,id LIMIT %s""",
                (tenant_id,query,known_at,known_at,valid_at,valid_at,min(limit*10,100)),
            )
            for rank,(mid,) in enumerate(rows,1):
                ranked.setdefault(mid,[]).append(("sql",rank))

        if self.graph and anchor_entity:
            try:
                subjects=set(self.graph.neighbors(tenant_id=tenant_id,from_entity=anchor_entity,hops=1))
                subjects.add(anchor_entity)
                graph_rank=0
                for subject in sorted(subjects):
                    rows=self.store.recall(tenant_id=tenant_id,subject_id=subject,
                                           valid_at=valid_at,known_at=known_at)
                    for row in rows:
                        graph_rank+=1
                        ranked.setdefault(row["id"],[]).append(("graph",graph_rank))
            except Exception as exc:
                degraded["graph"]=f"{type(exc).__name__}: {str(exc)[:240]}"

        if not ranked:
            return SearchReport((),(),degraded)
        mids=sorted(ranked)
        rows=self.store.conn.execute(
            """SELECT id::text, subject_id,predicate,value,source_id
               FROM memory_claims WHERE id=ANY(%s::uuid[]) AND tenant_id=%s
                 AND recorded_at<=%s AND (superseded_at IS NULL OR superseded_at>%s)
                 AND valid_from<=%s AND (valid_to IS NULL OR valid_to>%s)""",
            (mids,tenant_id,known_at,known_at,valid_at,valid_at))
        results=[]
        for mid,subject,pred,value,source in rows:
            channels=ranked[mid]
            score=sum(1/(k+rank) for _,rank in channels)
            results.append(MemoryHit(mid,subject,pred,value,source,score,
                                     tuple(sorted({channel for channel,_ in channels}))))
        hits=tuple(sorted(results,key=lambda x:(-x.score,x.memory_id))[:limit])
        used=tuple(sorted({channel for hit in hits for channel in hit.channels}))
        return SearchReport(hits,used,degraded)
