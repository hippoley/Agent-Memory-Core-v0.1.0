"""Public frontend README references must match the committed UI."""
from pathlib import Path
import re
from fastapi.testclient import TestClient
from agent_memory_core.api import create_app

ROOT=Path(__file__).resolve().parents[1]


def test_both_readmes_use_main_and_disclose_unreleased_demo_boundary():
    for name in ("README.md", "docs/START-HERE.zh-CN.md"):
        path=ROOT/name
        content=path.read_text(encoding="utf-8")
        assert "Memory Observatory" in content
        assert "PR #56" in content
        assert "git clone --branch main --single-branch" in content
        assert "feat/identity-foundation-schema-20261118" not in content
        assert "AGE" in content and "Temporal" in content
        for href in re.findall(r"\]\(([^)]+)\)",content):
            if href.startswith(("https://","http://","mailto:","#")):
                continue
            rel=href.split("#",1)[0].split("?",1)[0]
            if rel:
                assert (path.parent/rel).is_file(), f"Missing {name}: {rel}"


def test_static_observatory_is_served_by_the_existing_api_origin():
    app=create_app(api_key="k"*32,tenant_id="t",store_factory=lambda:None)
    client=TestClient(app)
    response=client.get("/dashboard/observatory.html")
    assert response.status_code==200
    assert "text/html" in response.headers["content-type"]
    assert "Memory Observatory" in response.text
    assert "/v1/memories?limit=500&include_superseded=true" in response.text
    assert "/v1/events?limit=100" in response.text
    assert client.get("/v1/memories").status_code in (401,403)
