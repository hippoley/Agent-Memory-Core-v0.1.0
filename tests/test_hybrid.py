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


def test_migrate_path_targets_repo_root():
    source=(Path(__file__).resolve().parents[1]/"src/agent_memory_core/postgres.py").read_text()
    assert 'parents[2] / "sql/postgresql/001_core.sql"' in source
