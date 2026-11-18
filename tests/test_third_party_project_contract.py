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


def test_external_consumer_installs_the_exact_ci_built_wheel():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / '.github/workflows/product-e2e.yaml').read_text(encoding='utf-8')
    assert 'wheel="$(find /tmp/amc-wheel' in workflow
    assert 'pip install "${wheel}[api]"' in workflow
    assert "pip install --find-links /tmp/amc-wheel 'agent-memory-core[api]==0.1.0'" not in workflow
    assert "(cd /tmp && /tmp/amc-consumer-venv/bin/python -m third_party_memory_agent)" in workflow


def test_external_consumer_capabilities_match_public_api_schema():
    from agent_memory_core.contracts import capabilities
    reported = capabilities(temporal=True)
    assert reported
    assert all(set(("name", "available", "reason")) <= item.keys() for item in reported)
    root = Path(__file__).resolve().parents[1]
    consumer = (root / 'examples/third_party_memory_agent/src/third_party_memory_agent/__main__.py').read_text(encoding='utf-8')
    assert 'item["available"]' in consumer
    assert 'item["enabled"]' not in consumer
