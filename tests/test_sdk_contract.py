"""Public SDK contract checks with an HTTP transport, not in-process DB calls."""
import json
import pytest

httpx=pytest.importorskip("httpx")
from agent_memory_core.sdk import MemoryClient, MemoryAPIError


def test_sdk_authorization_write_read_replay_and_correction():
    requests=[]
    def server(request):
        requests.append(request)
        assert request.headers["authorization"]=="Bearer secret"
        if request.url.path=="/v1/ready":
            return httpx.Response(200,json={"status":"ready"})
        if request.url.path=="/v1/memories" and request.method=="POST":
            body=json.loads(request.content)
            assert body["source_id"]=="dialogue:1"
            return httpx.Response(201,json={"id":"claim-1","tenant_id":"tenant-one"})
        if request.url.path=="/v1/memories/bedroom":
            return httpx.Response(200,json={"contract_version":"1.0.0","items":[{"id":"claim-1"}]})
        if request.url.path=="/v1/events":
            return httpx.Response(200,json={"items":[{"event_id":"a"}],"next_cursor":"a","has_more":False})
        return httpx.Response(404,json={"detail":"missing"})

    with MemoryClient("http://memory.local","secret",transport=httpx.MockTransport(server)) as sdk:
        assert sdk.ready()["status"]=="ready"
        created=sdk.remember(subject_id="bedroom",predicate="preference",
            value={"noise":"quiet"},source_id="dialogue:1",
            valid_from="2026-11-10T00:00:00Z")
        assert created["id"]=="claim-1"
        assert sdk.recall("bedroom")["items"][0]["id"]=="claim-1"
        assert sdk.events(after="a")["next_cursor"]=="a"
        with pytest.raises(MemoryAPIError) as err:
            sdk.sources("missing")
        assert err.value.code=="NOT_FOUND" and err.value.status_code==404
    assert len(requests)==5
    assert requests[-2].url.params["after"]=="a"


def test_sdk_no_internal_tenant_selection_and_no_exposed_credentials():
    def server(req):
        assert "tenant_id" not in req.url.params
        assert "tenant_id" not in json.loads(req.content)
        return httpx.Response(201,json={"id":"one"})
    with MemoryClient("http://example.local","secret",transport=httpx.MockTransport(server)) as sdk:
        sdk.remember(subject_id="room",predicate="note",value={"a":1},
            source_id="s",valid_from="2026-11-10T00:00:00Z")


def test_sdk_error_codes_for_auth_and_unavailable():
    for status,code in [(401,"UNAUTHENTICATED"),(403,"FORBIDDEN"),
                        (422,"VALIDATION_ERROR"),(503,"UNAVAILABLE")]:
        with MemoryClient("http://localhost","secret",transport=httpx.MockTransport(
                lambda _:httpx.Response(status,json={"detail":"denied"}))) as sdk:
            with pytest.raises(MemoryAPIError) as err:
                sdk.ready()
        assert err.value.code==code and "denied" in str(err.value)
