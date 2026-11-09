import pytest
from agent_memory_core.contracts import capabilities,aware_iso

def test_capability_degradation():
    items={x["name"]:x for x in capabilities()}
    assert items["memory.claims"]["available"]
    assert not items["workflow.temporal"]["available"]
    assert not items["memory.graph"]["available"]

def test_timezone_validation():
    with pytest.raises(ValueError): aware_iso("2026-11-09T12:00:00")
    assert aware_iso("2026-11-09T12:00:00+08:00").utcoffset().total_seconds()==0

def test_single_tenant_api_and_fails_closed():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    with pytest.raises(RuntimeError):
        create_app(api_key="short",tenant_id="home")
    class FakeStore:
        def remember(self, **kw):
            assert kw["tenant_id"]=="home"
            return "id1"
        def recall(self, **kw):
            assert kw["tenant_id"]=="home"
            return [{"id":"id1"}]
        def close(self): pass
    app=create_app(api_key="secret-token-with-more-than-24-characters",
                   tenant_id="home",store_factory=FakeStore)
    client=TestClient(app)
    assert client.get("/v1/health").status_code==200
    assert client.get("/v1/capabilities").status_code==401
    assert client.get("/v1/capabilities",headers={"Authorization":"Bearer bad"}).status_code==403
    headers={"Authorization":"Bearer secret-token-with-more-than-24-characters"}
    assert client.get("/v1/capabilities",headers=headers).status_code==200
    data={"subject_id":"room","predicate":"preference","value":{"text":"quiet"},
          "source_id":"user","valid_from":"2026-11-01T00:00:00Z"}
    assert client.post("/v1/memories",json=data,headers=headers).status_code==201
    assert client.get("/v1/memories/room",headers=headers).json()["items"]==[{"id":"id1"}]
    assert client.post("/v1/memories",json={**data,"tenant_id":"other"},headers=headers).status_code==422


def test_search_endpoint_uses_tenant_and_returns_channel_diagnostics():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from agent_memory_core.api import create_app

    class Search:
        def search_detailed(self, **kw):
            assert kw["tenant_id"]=="home"
            assert kw["query"]=="window noise"
            return SimpleNamespace(
                hits=[SimpleNamespace(memory_id="m1",subject_id="room",
                    predicate="noise",value={"db":True},source_id="sensor",
                    score=0.03,channels=("sql","graph"))],
                channels_used=("graph","sql"),degraded_channels={"vector":"not configured"})
        def close(self): pass

    app=create_app(api_key="secret-token-with-more-than-24-characters",
        tenant_id="home",search_factory=Search)
    client=TestClient(app)
    headers={"Authorization":"Bearer secret-token-with-more-than-24-characters"}
    result=client.post("/v1/search",headers=headers,json={"query":"window noise"})
    assert result.status_code==200,result.text
    body=result.json()
    assert body["items"][0]["memory_id"]=="m1"
    assert body["channels_used"]==["graph","sql"]
    assert body["degraded_channels"]["vector"]=="not configured"


def test_search_endpoint_reports_unconfigured_adapter():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    app=create_app(api_key="secret-token-with-more-than-24-characters",tenant_id="home")
    result=TestClient(app).post("/v1/search",
        headers={"Authorization":"Bearer secret-token-with-more-than-24-characters"},
        json={"query":"hello"})
    assert result.status_code==503


def test_swagger_and_openapi_are_published_with_bearer_security():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    app=create_app(
        api_key="secret-token-with-more-than-24-characters",
        tenant_id="home",
    )
    client=TestClient(app)
    docs=client.get("/docs")
    assert docs.status_code==200
    assert "swagger-ui" in docs.text.lower()
    spec=client.get("/openapi.json")
    assert spec.status_code==200
    body=spec.json()
    assert body["openapi"].startswith("3.")
    assert set(body["paths"]) >= {
        "/v1/health","/v1/ready","/v1/capabilities",
        "/v1/memories","/v1/memories/{subject_id}","/v1/search"
    }
    assert "HTTPBearer" in body["components"]["securitySchemes"]
    assert body["paths"]["/v1/memories"]["post"]["security"] == [{"HTTPBearer": []}]
    assert "HTTPBearer" not in body["paths"]["/v1/health"]["get"].get("security", [])
