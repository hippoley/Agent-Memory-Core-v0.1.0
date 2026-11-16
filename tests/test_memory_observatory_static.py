"""Guardrails for the read-only Memory Observatory static page."""
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "src/agent_memory_core/resources/dashboard/observatory.html"


def test_observatory_is_packaged_with_dashboard():
    assert PAGE.is_file()
    html = PAGE.read_text(encoding="utf-8")
    assert "Agent Memory Observatory" in html
    assert 'id="art"' in html
    assert 'id="auth"' in html
    assert 'id="time"' in html
    assert 'id="detail"' in html


def test_observatory_uses_only_existing_read_endpoints():
    html = PAGE.read_text(encoding="utf-8")
    assert '/v1/memories?limit=500&include_superseded=true' in html
    assert '/v1/events?limit=100' in html
    assert 'mode:"demo"' in html
    assert 'mode:"live"' in html
    assert 'localStorage' not in html
    assert '<script src=' not in html
    assert 'no server-side retrieval' in html.lower() or 'not a server-side hybrid retrieval' in html.lower()
