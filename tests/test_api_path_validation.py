"""Malformed memory ids must fail at the edge, never inside PostgreSQL.

A non-UUID id used to reach SQL, raise InvalidTextRepresentation and turn into
an HTTP 500 that also tore down the keep-alive connection, failing the next
request on that connection. These tests pin the 422 contract and prove the
store is never touched.
"""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from agent_memory_core.api import create_app

API_KEY = "k" * 32


def _app():
    def unreachable():
        raise AssertionError("store must not be opened for a malformed id")

    return create_app(api_key=API_KEY, tenant_id="t", store_factory=unreachable,
                      capability_flags={"semantic": True})


@pytest.fixture()
def client():
    return TestClient(_app(), headers={"Authorization": f"Bearer {API_KEY}"})


@pytest.mark.parametrize("bad_id", ["None", "abc", "1", "not-a-uuid"])
def test_put_embedding_rejects_non_uuid(client, bad_id):
    response = client.put(f"/v1/memories/{bad_id}/embedding",
                          json={"model_id": "test-512", "embedding": [0.1] * 512})
    assert response.status_code == 422
    assert "uuid" in response.json()["detail"].lower()


@pytest.mark.parametrize("bad_id", ["None", "abc", "not-a-uuid"])
def test_memory_sources_rejects_non_uuid(client, bad_id):
    response = client.get(f"/v1/memories/{bad_id}/sources")
    assert response.status_code == 422
    assert "uuid" in response.json()["detail"].lower()


@pytest.mark.parametrize("bad_id", ["None", "abc", "not-a-uuid"])
def test_forget_rejects_non_uuid(client, bad_id):
    response = client.delete(f"/v1/memories/{bad_id}")
    assert response.status_code == 422


def test_connection_survives_rejected_id(client):
    """A 422 must not poison the connection for the following request."""
    assert client.put("/v1/memories/None/embedding",
                      json={"model_id": "test-512", "embedding": [0.1] * 512}
                      ).status_code == 422
    health = client.get("/v1/health")
    assert health.status_code == 200
    assert health.json()["status"] == "alive"
