"""Live loopback HTTP gateway test: distinct credentials, revoke and idempotency.

This is a REFERENCE resource server, not a physical device or proof of a
production gateway's own enforcement. No AMC database or remote network.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, request

import pytest

from agent_memory_core.durable_effects import ProviderRejected
from agent_memory_core.http_effect_provider import HTTPDurableEffectProvider
from agent_memory_core.provider_contract import ProviderContractError, validate_provider


@pytest.fixture
def gateway():
    state = {"effects": {}, "exec_count": 0, "actuate_allowed": True,
             "observed": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _reply(self, code, data):
            payload = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            state["observed"].append(("POST", self.headers.get("Authorization")))
            if self.path != "/v1/effects":
                return self._reply(404, {})
            if self.headers.get("Authorization") != "Bearer actuator" or not state["actuate_allowed"]:
                return self._reply(403, {"error": "revoked or wrong scope"})
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            key = data["idempotency_key"]
            if key not in state["effects"]:
                state["exec_count"] += 1
                state["effects"][key] = {
                    "provider_reference": "receipt-" + str(state["exec_count"]),
                    "result": {"device": "simulated", "value": data["request"]["value"]}}
            return self._reply(200, state["effects"][key])

        def do_GET(self):
            state["observed"].append(("GET", self.headers.get("Authorization")))
            if not self.path.startswith("/v1/effects/"):
                return self._reply(404, {})
            if self.headers.get("Authorization") != "Bearer auditor":
                return self._reply(403, {"error": "lookup scope required"})
            key = self.path.split("/v1/effects/", 1)[1]
            if key not in state["effects"]:
                return self._reply(200, {"outcome": "not_found"})
            return self._reply(200, {
                "outcome": "committed", **state["effects"][key]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port) + "/v1", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_real_http_resource_server_rejects_revoked_actuation_but_allows_read_only_lookup(gateway):
    url, state = gateway
    provider = HTTPDurableEffectProvider(
        base_url=url, actuation_token="actuator", lookup_token="auditor")
    assert validate_provider(provider).lookup_consistency == "strong"

    first = provider.execute({"value": 26}, idempotency_key="stable-id-1")
    same = provider.execute({"value": 26}, idempotency_key="stable-id-1")
    assert first == same
    assert state["exec_count"] == 1
    state["actuate_allowed"] = False
    with pytest.raises(ProviderRejected, match="denied"):
        provider.execute({"value": 25}, idempotency_key="stable-id-2")
    assert state["exec_count"] == 1

    # Reconciliation is a real HTTP GET authenticated with a different token.
    value = provider.reconcile({"value": 26}, idempotency_key="stable-id-1",
                               provider_reference=None)
    assert value["outcome"] == "committed"
    assert value["provider_reference"] == first["provider_reference"]
    assert state["exec_count"] == 1
    assert ("POST", "Bearer auditor") not in state["observed"]
    assert ("GET", "Bearer actuator") not in state["observed"]
    assert ("POST", "Bearer actuator") in state["observed"]
    assert ("GET", "Bearer auditor") in state["observed"]


def test_lookup_not_found_only_from_explicit_authoritative_body(gateway):
    url, state = gateway
    provider = HTTPDurableEffectProvider(
        base_url=url, actuation_token="actuator", lookup_token="auditor")
    assert provider.reconcile({}, idempotency_key="absent", provider_reference=None) == {
        "outcome": "not_found"}
    wrong = HTTPDurableEffectProvider(base_url=url,
        actuation_token="auditor", lookup_token="actuator")
    with pytest.raises(ProviderContractError, match="HTTP 403"):
        wrong.reconcile({}, idempotency_key="absent", provider_reference=None)
    assert state["exec_count"] == 0


@pytest.mark.parametrize("url", ["http://remote.example/v1", "https://u:p@site.example/v1",
                                 "ftp://site.example/v1", "https://site.example/v1?token=x"])
def test_unsafe_gateway_urls_rejected(url):
    with pytest.raises(ValueError):
        HTTPDurableEffectProvider(base_url=url, actuation_token="write",
                                  lookup_token="read")


def test_never_reuse_same_token_for_read_and_write(gateway):
    url, _ = gateway
    with pytest.raises(ValueError, match="Distinct"):
        HTTPDurableEffectProvider(base_url=url, actuation_token="shared",
                                  lookup_token="shared")
