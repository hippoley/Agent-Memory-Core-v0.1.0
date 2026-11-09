import io
import json
from agent_memory_core.client import MemoryClient, MemoryAPIError


class FakeOpener:
    def __init__(self, payload, status=200):
        self.payload=payload
        self.status=status
        self.request=None
    def open(self, request, timeout):
        self.request=request
        if self.status>=400:
            from urllib.error import HTTPError
            raise HTTPError(request.full_url,self.status,"error",{},io.BytesIO(
                json.dumps({"detail":"conflict"}).encode()))
        return io.BytesIO(json.dumps(self.payload).encode())


def test_python_client_sends_auth_and_idempotency_header():
    opener=FakeOpener({"id":"memory-1"})
    client=MemoryClient(base_url="http://127.0.0.1:8765",
                        api_key="test-key",opener=opener)
    result=client.remember({"subject_id":"self","predicate":"language",
                            "value":{"preferred":"zh"},"source_id":"sdk-test"},
                           idempotency_key="sdk-write-001")
    assert result["id"]=="memory-1"
    assert opener.request.get_header("Authorization")=="Bearer test-key"
    assert opener.request.get_header("Idempotency-key")=="sdk-write-001"


def test_python_client_maps_http_conflict_to_typed_error():
    opener=FakeOpener({"detail":"conflict"},status=409)
    client=MemoryClient(base_url="http://localhost:8765",
                        api_key="test-key",opener=opener)
    try:
        client.remember({"subject_id":"self"},idempotency_key="same-key")
    except MemoryAPIError as exc:
        assert exc.status_code==409
        assert exc.detail=="conflict"
    else:
        raise AssertionError("Expected MemoryAPIError")
