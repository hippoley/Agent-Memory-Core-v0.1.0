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
                                {"text":"quiet nights"},"source-1")])
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
