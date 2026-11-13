"""Hybrid retrieval with explicit optional-channel degradation.

SQL is the required canonical channel. Vector and graph are optional indexes:
their failures are surfaced in diagnostics while healthy channels continue.
No index result bypasses canonical tenant/time revalidation.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import nullcontext


_STOPWORDS = frozenset("""
a an the is are was were be been being do does did doing i me my mine you your
yours it its of to in on at for with and or but what which who whom whose when
where why how many much this that these those have has had if not no as from by
about there here they them he she his her we our us can could would should will
""".split())

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_WORD = re.compile(r"[a-z0-9_]{2,}")


def query_terms(query: str, limit: int = 10) -> list[str]:
    """Split a natural-language query into matchable terms.

    The lexical channel used to compare the whole query as one literal
    substring, so "主卧温度" never matched a claim about "主卧" and an English
    question never matched anything at all. Chinese has no spaces, so CJK runs
    are cut into overlapping bigrams — enough to recall a two-character subject
    without shipping a segmenter. Ranking still favours claims that match more
    terms, and the literal whole-query match remains the highest-scoring case.
    """
    if not isinstance(query, str):
        return []
    terms: list[str] = []
    for run in _CJK_RUN.findall(query):
        if len(run) <= 2:
            terms.append(run)
        else:
            terms.extend(run[i:i + 2] for i in range(len(run) - 1))
    terms.extend(word for word in _LATIN_WORD.findall(query.lower())
                 if word not in _STOPWORDS)
    seen: set[str] = set()
    picked: list[str] = []
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        picked.append(term)
        if len(picked) >= limit:
            break
    return picked


def _channel_transaction(store):
    """Rollback a failed optional SQL index query before canonical SQL continues."""
    factory=getattr(getattr(store, "conn", None), "transaction", None)
    return factory() if callable(factory) else nullcontext()

@dataclass(frozen=True)
class MemoryHit:
    memory_id: str
    subject_id: str
    predicate: str
    value: dict
    source_id: str
    score: float
    channels: tuple[str, ...]
    valid_from: datetime | None = None
    recorded_at: datetime | None = None

@dataclass(frozen=True)
class SearchReport:
    hits: tuple[MemoryHit, ...]
    channels_used: tuple[str, ...]
    degraded_channels: dict[str, str]

_DEFAULT_CHANNEL_WEIGHTS = {"vector": 1.0, "sql": 0.8, "fts": 0.4, "graph": 0.6}


class HybridMemorySearch:
    def __init__(self, store, graph=None, *, channel_weights: dict | None = None,
                 query_aliases: dict[str, str] | None = None):
        self.store = store
        self.graph = graph
        self.query_aliases = {}
        for alias, predicate in (query_aliases or {}).items():
            if (not isinstance(alias, str) or not 1 <= len(alias) <= 32
                    or not isinstance(predicate, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", predicate)):
                raise ValueError("query_aliases must map short natural-language strings to valid predicate names")
            self.query_aliases[alias] = predicate
        # Channels are not equally trustworthy. Exact-match lexical hits are
        # precise; dense vectors find paraphrases but rank noisy neighbours
        # high; the simple-dictionary FTS is the weakest signal. Equal-weight
        # RRF let the noisiest channel crowd out correct hits, so every channel
        # contributes weight/(k+rank) instead.
        self.channel_weights = dict(_DEFAULT_CHANNEL_WEIGHTS)
        if channel_weights:
            self.channel_weights.update(
                {str(k): float(v) for k, v in channel_weights.items()})

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
                with _channel_transaction(self.store):
                    vector_rows=self.store.vector_search(
                        tenant_id=tenant_id,model_id=model_id,vector=embedding,
                        limit=min(limit*10,100),valid_at=valid_at,known_at=known_at)
                for rank,(mid,_) in enumerate(vector_rows,1):
                    ranked.setdefault(mid,[]).append(("vector",rank))
            except Exception as exc:
                degraded["vector"]=f"{type(exc).__name__}: {str(exc)[:240]}"

        # Search the rebuildable ranked full-text index when the backend
        # supports it. Its results are always revalidated below against
        # canonical tenant/known-at/valid-at state.
        fts_search=getattr(self.store,"fulltext_search",None)
        if query and query.strip() and callable(fts_search):
            try:
                with _channel_transaction(self.store):
                    fts_rows=fts_search(
                        tenant_id=tenant_id,query=query,
                        limit=min(limit*10,100),
                        valid_at=valid_at,known_at=known_at)
                for rank,mid in enumerate(fts_rows,1):
                    ranked.setdefault(mid,[]).append(("fts",rank))
            except Exception as exc:
                degraded["fts"]=f"{type(exc).__name__}: {str(exc)[:240]}"

        if query and query.strip():
            # POSITION treats every term literally; no LIKE wildcard or ESCAPE
            # dialect ambiguity across PostgreSQL versions. Each term is matched
            # against subject, predicate and value, and claims matching more
            # terms rank first, so a full question still recalls the claim that
            # carries its key nouns instead of nothing.
            terms = query_terms(query)
            # Domain synonyms supplement—not replace—the lexical candidate
            # search. They are configured data, never guessed global synonyms.
            # Match original query, not arbitrary coincidental CJK bigrams.
            for alias, predicate in self.query_aliases.items():
                if alias in query and predicate not in terms and len(terms) < 20:
                    terms.append(predicate)
            if terms:
                match = ("(position(lower(%s) in lower(subject_id||' '||predicate||' '"
                         "||coalesce(value::text,''))) > 0)")
                score = " + ".join(f"(CASE WHEN {match} THEN 1 ELSE 0 END)" for _ in terms)
                sql = (
                    "SELECT id::text FROM memory_claims\n"
                    f"   WHERE tenant_id=%s AND ({score}) > 0\n"
                    "     AND recorded_at<=%s AND (superseded_at IS NULL OR superseded_at>%s)\n"
                    "     AND valid_from<=%s AND (valid_to IS NULL OR valid_to>%s)\n"
                    f"   ORDER BY ({score}) DESC, recorded_at DESC, id LIMIT %s")
                params = ([tenant_id] + terms + [known_at, known_at, valid_at, valid_at]
                          + terms + [min(limit * 10, 100)])
                rows = self.store.conn.execute(sql, params)
                for rank, (mid,) in enumerate(rows, 1):
                    ranked.setdefault(mid, []).append(("sql", rank))

        if self.graph and anchor_entity:
            try:
                with _channel_transaction(self.store):
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
            """SELECT id::text, subject_id,predicate,value,source_id,valid_from,recorded_at
               FROM memory_claims WHERE id=ANY(%s::uuid[]) AND tenant_id=%s
                 AND recorded_at<=%s AND (superseded_at IS NULL OR superseded_at>%s)
                 AND valid_from<=%s AND (valid_to IS NULL OR valid_to>%s)""",
            (mids,tenant_id,known_at,known_at,valid_at,valid_at))
        results=[]
        for row in rows:
            mid,subject,pred,value,source = row[:5]
            valid_from = row[5] if len(row) > 5 else None
            recorded_at = row[6] if len(row) > 6 else None
            channels=ranked[mid]
            score=sum(self.channel_weights.get(channel, 0.5) / (k + rank)
                      for channel, rank in channels)
            results.append(MemoryHit(mid,subject,pred,value,source,score,
                                     tuple(sorted({channel for channel,_ in channels})),
                                     valid_from,recorded_at))
        hits=tuple(sorted(results,key=lambda x:(-x.score,x.memory_id))[:limit])
        used=tuple(sorted({channel for hit in hits for channel in hit.channels}))
        return SearchReport(hits,used,degraded)
