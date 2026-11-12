"""Every documented operation must tell a caller what it is for.

The rule this file enforces is deliberately mechanical: if a route is
registered, it has a user story. Documentation that drifts behind the code is
worse than no documentation, so the check runs in CI rather than in review.
"""
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from agent_memory_core.api import create_app
from agent_memory_core.api_docs import SERVICE_DESCRIPTION, STORIES

DOCS_HTML = Path(__file__).resolve().parents[1] / "src" / "agent_memory_core" / "resources" / "docs" / "api.html"


def _app():
    return create_app(api_key="k" * 32, tenant_id="t", store_factory=lambda: None,
                      capability_flags={"semantic": True})


def _documented_routes(app):
    """(METHOD, path) pairs that appear in the OpenAPI schema."""
    return {
        (method.upper(), route.path)
        for route in app.routes
        if getattr(route, "include_in_schema", True) and getattr(route, "methods", None)
        for method in route.methods
        if method in {"GET", "POST", "PUT", "DELETE", "PATCH"} and route.path.startswith("/v1/")
    }


def test_every_operation_has_a_user_story():
    app = _app()
    routes = _documented_routes(app)
    missing = sorted(routes - set(STORIES))
    assert not missing, f"endpoints without a user story: {missing}"


def test_no_story_points_at_a_removed_endpoint():
    stale = sorted(set(STORIES) - _documented_routes(_app()))
    assert not stale, f"stories for routes that no longer exist: {stale}"


def test_stories_are_complete():
    for key, story in STORIES.items():
        assert story.persona and story.goal and story.value, key
        assert story.steps, f"{key} needs at least one concrete step"
        assert story.example.strip(), f"{key} needs a runnable example"


def test_openapi_carries_story_and_keeps_technical_description():
    spec = _app().openapi()
    operations = [op for path in spec["paths"].values()
                  for method, op in path.items()
                  if method in {"get", "post", "put", "delete", "patch"}]
    assert operations
    for operation in operations:
        story = operation["x-user-story"]
        assert story["persona"] and story["goal"] and story["value"]
        assert "User story" in operation["description"]
        assert "```bash" in operation["description"]


def test_service_description_explains_auth_and_error_contract():
    for phrase in ("Authorization: Bearer", "422", "503", "valid_from"):
        assert phrase in SERVICE_DESCRIPTION


def test_docs_entry_is_the_stock_swagger_ui():
    response = TestClient(_app()).get("/docs")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "SwaggerUIBundle" in response.text


def test_manual_is_served_without_a_cdn():
    client = TestClient(_app())
    response = client.get("/docs/manual")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "接口手册" in body
    assert "cdn.jsdelivr.net" not in body  # must work on an isolated network
    assert "/openapi.json" in body


def test_swagger_ui_is_kept_as_an_alias():
    response = TestClient(_app()).get("/docs/swagger")
    assert response.status_code == 200
    assert "SwaggerUIBundle" in response.text


def test_manual_asset_is_packaged():
    assert DOCS_HTML.is_file()
    packaged = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert "resources/docs/" in packaged
