"""The console is served same-origin so the browser needs no CORS and no proxy."""
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from agent_memory_core.api import create_app

DASHBOARD = Path(__file__).resolve().parents[1] / "src" / "agent_memory_core" / "resources" / "dashboard" / "index.html"


def _client():
    app = create_app(api_key="k" * 32, tenant_id="t", store_factory=lambda: None,
                     capability_flags={"semantic": True})
    return TestClient(app)


def test_dashboard_is_served_without_auth():
    """Static assets must load before the key is entered, or the gate never renders."""
    response = _client().get("/dashboard/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "记忆看板" in body
    assert "/v1/capabilities" in body  # the page really talks to this API


def test_dashboard_asset_exists_in_package_data():
    assert DASHBOARD.is_file()
    assert "resources/dashboard/index.html" in (
        Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")


def test_root_redirects_to_dashboard():
    response = _client().get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/dashboard/"


def test_api_still_requires_bearer_while_dashboard_is_public():
    client = _client()
    assert client.get("/v1/health").status_code == 200
    assert client.get("/v1/memories").status_code in (401, 403)
