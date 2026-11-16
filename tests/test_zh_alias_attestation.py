"""Real HTTP attestation of effective alias configuration, not CLI claims."""
import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from agent_memory_core.api import create_app


KEY="evaluation-local-secret-key-strong-123"


class Search:
    def __init__(self, aliases):
        self.query_aliases=aliases
        self.closed=False

    def close(self):
        self.closed=True


def _client(search):
    app=create_app(api_key=KEY,tenant_id="eval-t",search_factory=lambda:search)
    return TestClient(app)


def test_attestation_reports_active_search_aliases_and_closes_adapter():
    mapping={"温度":"temperature_preference","灯光":"light_preference"}
    service=Search(mapping)
    client=_client(service)
    response=client.get("/v1/evaluation/search-config",headers={"Authorization":"Bearer "+KEY})
    assert response.status_code==200,response.text
    body=response.json()
    expected_hash=hashlib.sha256(json.dumps(mapping,ensure_ascii=False,sort_keys=True,
                                           separators=(",",":")).encode("utf-8")).hexdigest()
    assert body["aliases_enabled"] is True
    assert body["alias_count"]==2
    assert body["alias_sha256"]==expected_hash
    assert body["tenant_id"]=="eval-t"
    assert service.closed


def test_attestation_disabled_mapping_is_distinct_and_deterministic():
    service=Search({})
    client=_client(service)
    response=client.get("/v1/evaluation/search-config",headers={"Authorization":"Bearer "+KEY})
    assert response.status_code==200
    assert response.json()["aliases_enabled"] is False
    assert response.json()["alias_count"]==0
    assert service.closed


def test_attestation_requires_valid_bearer():
    service=Search({"温度":"temperature_preference"})
    response=_client(service).get("/v1/evaluation/search-config")
    assert response.status_code in (401,403)
    assert not service.closed


def test_attestation_fails_closed_when_adapter_cannot_expose_aliases():
    class Unverifiable:
        def __init__(self):self.closed=False
        def close(self):self.closed=True
    service=Unverifiable()
    client=_client(service)
    response=client.get("/v1/evaluation/search-config",headers={"Authorization":"Bearer "+KEY})
    assert response.status_code==409
    assert service.closed


def test_attestation_refuses_missing_search_factory():
    client=TestClient(create_app(api_key=KEY,tenant_id="eval-t"))
    response=client.get("/v1/evaluation/search-config",headers={"Authorization":"Bearer "+KEY})
    assert response.status_code==503
