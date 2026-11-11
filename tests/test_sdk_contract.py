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


def test_sdk_resumable_sse_parses_frames_and_cursors():
    received=[]
    def server(request):
        assert request.url.path=="/v1/events/stream"
        assert request.url.params["after"]=="previous"
        received.append(request)
        return httpx.Response(200,content=(
            b": keepalive\n\n"
            b"id: event-2\n"
            b"event: memory.claim.created\n"
            b'data: {"event_id":"event-2","payload":{"subject_id":"bedroom"}}\n\n'
        ),
        headers={"content-type":"text/event-stream"})
    with MemoryClient("http://localhost","secret",transport=httpx.MockTransport(server)) as sdk:
        events=list(sdk.stream_events(after="previous",max_seconds=1))
    assert len(events)==1
    assert events[0]["id"]=="event-2"
    assert events[0]["data"]["payload"]["subject_id"]=="bedroom"
    assert len(received)==1


def test_sdk_entity_and_observation_contract():
    calls=[]
    def server(request):
        calls.append((request.url.path,json.loads(request.content)))
        return httpx.Response(201,json={"accepted":True})
    with MemoryClient("http://localhost","secret",transport=httpx.MockTransport(server)) as sdk:
        sdk.add_entity(entity_id="room-1",entity_type="room",canonical_name="主卧")
        sdk.add_edge(from_entity="room-1",predicate="HAS_WINDOW",to_entity="window-1")
        sdk.observe(device_id="window-1",source_event_id="evt-1",
            observed_at="2026-11-10T00:00:00Z",property="angle",
            value=35.0,unit="degree",source_id="gateway")
    assert [path for path,_ in calls]==[
        "/v1/entities","/v1/edges","/v1/observations"]
    assert calls[2][1]["source_event_id"]=="evt-1"


def test_sdk_webhook_lifecycle_routes_and_one_time_secret():
    calls=[]
    def server(request):
        calls.append((request.method,request.url.path))
        if request.url.path=="/v1/webhooks" and request.method=="POST":
            return httpx.Response(201,json={"subscription_id":"sub-1","signing_secret":"one-time"})
        return httpx.Response(200,json={"items":[],"requeued":True})
    with MemoryClient("http://localhost","secret",
            transport=httpx.MockTransport(server)) as sdk:
        assert sdk.create_webhook(target_url="https://hooks.example.test/a",
            event_types=["memory.claim.created"])["signing_secret"]=="one-time"
        sdk.list_webhooks()
        sdk.webhook_deliveries("sub-1")
        sdk.replay_webhook_delivery("delivery-1")
        sdk.revoke_webhook("sub-1")
    assert calls==[
        ("POST","/v1/webhooks"),("GET","/v1/webhooks"),
        ("GET","/v1/webhooks/sub-1/deliveries"),
        ("POST","/v1/webhooks/deliveries/delivery-1/replay"),
        ("DELETE","/v1/webhooks/sub-1")]


def test_sdk_replays_dead_outbox_event_for_current_tenant():
    calls=[]
    def server(request):
        calls.append((request.method,request.url.path))
        return httpx.Response(200,json={"event_id":"event-1","requeued":True})
    with MemoryClient("http://localhost","secret",
            transport=httpx.MockTransport(server)) as sdk:
        assert sdk.replay_outbox_event("event-1")["requeued"] is True
    assert calls==[("POST","/v1/events/outbox/event-1/replay")]


@pytest.mark.parametrize("exception,code",[
    (httpx.ReadTimeout("slow peer"),"TIMEOUT"),
    (httpx.ConnectError("offline"),"TRANSPORT_ERROR"),
])
def test_sdk_normalizes_transport_failures_without_leaking_credentials(exception,code):
    def fail(request):
        raise exception
    with MemoryClient("http://localhost","private-token",
            transport=httpx.MockTransport(fail)) as sdk:
        with pytest.raises(MemoryAPIError) as exc:
            sdk.ready()
    assert exc.value.status_code==0
    assert exc.value.code==code
    assert "private-token" not in str(exc.value)


def test_sdk_rejects_invalid_success_json_and_accepts_empty_response():
    with MemoryClient("http://localhost","secret",transport=httpx.MockTransport(
            lambda _:httpx.Response(200,text="{incomplete"))) as sdk:
        with pytest.raises(MemoryAPIError) as exc:
            sdk.capabilities()
    assert exc.value.code=="INVALID_RESPONSE"
    with MemoryClient("http://localhost","secret",transport=httpx.MockTransport(
            lambda _:httpx.Response(204))) as sdk:
        assert sdk.capabilities() is None


def test_sdk_rejects_path_traversal_and_encodes_reserved_characters():
    requests=[]
    def server(request):
        requests.append(str(request.url))
        return httpx.Response(200,json={"items":[]})
    with MemoryClient("http://localhost","secret",
            transport=httpx.MockTransport(server)) as sdk:
        for invalid in ("../other","/admin","a/b","a\\\\b","", ".",".."):
            with pytest.raises(ValueError):
                sdk.recall(invalid)
        assert sdk.recall("卧室 #1?")["items"]==[]
    assert len(requests)==1
    assert "%23" in requests[0] and "%3F" in requests[0]
