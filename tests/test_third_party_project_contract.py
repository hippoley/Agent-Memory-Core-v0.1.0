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


def test_extended_product_api_image_installs_temporal_client():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / 'docker/Dockerfile.api').read_text(encoding='utf-8')
    assert '.[postgres,api,oidc,temporal]' in dockerfile, (
        'Product E2E starts Temporal through the API container: the image must install the temporal extra.'
    )


def test_third_party_consumer_result_has_no_unbound_identifiers():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'examples/third_party_memory_agent/src/third_party_memory_agent/__main__.py').read_text(encoding='utf-8')
    assert 'object_digest' not in source, 'Evidence digest must be defined by an actual upload or omitted.'
