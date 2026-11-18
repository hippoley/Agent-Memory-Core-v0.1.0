"""The example consumer must stay outside the server implementation boundary."""
import ast
from pathlib import Path


def test_third_party_consumer_uses_only_public_sdk_and_no_database_access():
    root = Path(__file__).resolve().parents[1] / "examples/third_party_memory_agent/src"
    sources = sorted(root.rglob("*.py"))
    assert sources
    imported = set()
    source_text = ""
    for path in sources:
        text = path.read_text(encoding="utf-8")
        source_text += text + "\n"
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")

    assert "agent_memory_core.sdk" in imported
    assert not any(
        name.startswith(("agent_memory_core.api", "agent_memory_core.identity",
                         "agent_memory_core.postgres", "agent_memory_core.temporal",
                         "agent_memory_core.worker"))
        for name in imported
    )
    assert not any(name.split(".")[0] in {
        "psycopg", "psycopg2", "asyncpg", "sqlalchemy"
    } for name in imported)
    assert "MEMORY_DSN" not in source_text
    assert "MEMORY_TEST_DSN" not in source_text
