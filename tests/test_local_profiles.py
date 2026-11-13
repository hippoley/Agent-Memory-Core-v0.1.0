"""Offline source-contract checks for local launcher, profiles and measurements."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")

def test_local_profiles_are_restrictive():
    spec = importlib.util.spec_from_file_location("launch_local", ROOT / "scripts/launch_local.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.PROFILES["lite"] == ("0", "0")
    assert module.PROFILES["standard"] == ("1", "1")
    assert module.PROFILES["full"] == ("1", "1")
    assert "token_urlsafe" in read("scripts/launch_local.py")
    assert ".env.local" in read(".gitignore")

def test_compose_overlay_does_not_publish_public_ports():
    app = read("compose.app.yaml")
    db = read("compose.yaml")
    assert '127.0.0.1:8765:8765' in app
    assert '127.0.0.1:55432:5432' in db
    assert "service_healthy" in app
    assert "MEMORY_API_KEY: ${MEMORY_API_KEY:?" in app
    assert 'memory_local_objects:' in app

def test_python_container_and_worker_are_optional():
    assert "local_entrypoint" in read("docker/Dockerfile.api")
    assert "store.migrate()" in read("src/agent_memory_core/local_entrypoint.py")
    assert "memory-worker" in read("compose.worker.yaml")
    assert "projection_worker" in read("compose.worker.yaml")
