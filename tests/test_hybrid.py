from pathlib import Path
import ast
from agent_memory_core.hybrid import HybridMemorySearch


def test_hybrid_module_loads():
    source=(Path(__file__).resolve().parents[1]/"src/agent_memory_core/hybrid.py").read_text()
    ast.parse(source)


def test_hybrid_rejects_unsafe_input():
    class Store: pass
    svc=HybridMemorySearch(Store())
    try:
        svc.search(tenant_id="",query="x")
    except ValueError:
        pass
    else:
        raise AssertionError("tenant required")
    try:
        svc.search(tenant_id="t",query="",embedding=[1.0])
    except ValueError:
        pass
    else:
        raise AssertionError("model ID required")


def test_migrate_schema_available_from_installable_package():
    from importlib import resources
    ddl=resources.files("agent_memory_core").joinpath("resources/001_core.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS memory_claims" in ddl
    assert "CREATE EXTENSION IF NOT EXISTS vector" in ddl


def test_vector_and_graph_failures_degrade_to_canonical_sql():
    class Cursor:
        def __init__(self, rows): self.rows=rows
        def __iter__(self): return iter(self.rows)
    class Conn:
        def execute(self, query, params):
            if "SELECT id::text FROM memory_claims" in query:
                return Cursor([("claim-1",)])
            if "SELECT id::text, subject_id" in query:
                return Cursor([("claim-1","room-1","preference",
                                {"text":"quiet nights"},"source-1",
                                "2026-01-01T00:00:00+00:00",
                                "2026-01-01T00:00:00+00:00")])
            raise AssertionError("Unexpected SQL")
    class Store:
        conn=Conn()
        def vector_search(self, **kwargs):
            raise ConnectionError("vector index unavailable")
    class Graph:
        def neighbors(self, **kwargs):
            raise TimeoutError("AGE unavailable")
    report=HybridMemorySearch(Store(),Graph()).search_detailed(
        tenant_id="tenant-1",query="quiet",embedding=[1.0],
        model_id="test",anchor_entity="room-1")
    assert len(report.hits)==1
    assert report.hits[0].memory_id=="claim-1"
    assert report.channels_used==("sql",)
    assert "vector" in report.degraded_channels
    assert "graph" in report.degraded_channels


def test_lexical_channel_matches_subject_and_predicate_too():
    # The SQL lexical channel must match every term against subject_id,
    # predicate and value — searching "主卧" must recall a claim whose value
    # never contains that wording, and an English question must still recall
    # the claim that carries its nouns.
    source=(Path(__file__).resolve().parents[1]/"src/agent_memory_core/hybrid.py").read_text()
    assert "subject_id||' '||predicate" in source
    assert "position(lower(%s) in lower(" in source
    assert "CASE WHEN" in source  # multi-term scoring, not whole-string containment


def test_terms_split_cjk_into_bigrams_and_drop_english_stopwords():
    from agent_memory_core.hybrid import query_terms
    assert query_terms("主卧温度")[:3] == ["主卧", "卧温", "温度"]
    terms = query_terms("What was the first issue I had with my new car?")
    assert "issue" in terms and "car" in terms
    assert "what" not in terms and "the" not in terms
    assert len(query_terms("a " * 60)) == 0


def test_canonical_sql_failure_is_not_hidden():
    import pytest
    class Conn:
        def execute(self, *args):
            raise ConnectionError("canonical store down")
    class Store:
        conn=Conn()
    with pytest.raises(ConnectionError, match="canonical store down"):
        HybridMemorySearch(Store()).search_detailed(
            tenant_id="tenant-1",query="anything")


def test_fts_query_is_or_joined_and_quoted():
    """A long question must not require every word in one claim to match.

    websearch_to_tsquery ANDs bare terms, which returned zero rows for real
    questions; OR + ts_rank_cd degrades gracefully instead of dropping them.
    """
    from agent_memory_core.postgres import PostgresMemory as Store
    built = Store._fts_query("How many days did I spend camping in Utah?")
    assert built is not None
    assert " | " in built and "&" not in built
    assert built == " | ".join(f"'{t}'" for t in built.replace("'", "").split(" | "))
    assert Store._fts_query("   ") is None


def test_channel_weights_change_fusion_order():
    """Equal-weight RRF let the noisy FTS channel outrank the precise channels.

    A claim returned by vector at rank 1 must beat one returned by fts at
    rank 1, and operators must be able to retune the balance per deployment.
    """
    from agent_memory_core.hybrid import HybridMemorySearch, _DEFAULT_CHANNEL_WEIGHTS

    search = HybridMemorySearch(None)
    assert search.channel_weights["vector"] > search.channel_weights["fts"]
    tuned = HybridMemorySearch(None, channel_weights={"fts": 5.0})
    assert tuned.channel_weights["fts"] == 5.0
    assert tuned.channel_weights["vector"] == _DEFAULT_CHANNEL_WEIGHTS["vector"]
    for weight in search.channel_weights.values():
        assert weight > 0
