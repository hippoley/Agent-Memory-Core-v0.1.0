"""The public Python SDK can reach the domain-neutral, evidence-grounded API.

Uses a strict transport fake: this is an SDK contract test, NOT a real LLM result.
"""
import json
import pytest

httpx=pytest.importorskip("httpx")
from agent_memory_core.sdk import MemoryClient


def test_sdk_grounded_revision_sends_opt_in_and_context_to_api():
    recorded=[]
    def responder(req):
        recorded.append((req.url.path,json.loads(req.content)))
        return httpx.Response(200,json={"created":[],"replaced":[],"skipped":1})
    with MemoryClient("http://127.0.0.1:8765","local-key",
            transport=httpx.MockTransport(responder)) as client:
        response=client.process(
            text="Supplier A now settles quarterly, not monthly",
            source_id="conversation:2",
            context_turns=["Supplier A settles monthly"],
            ground_with_memory=True,
        )
    assert response["skipped"]==1
    assert recorded[0][0]=="/v1/memories/process"
    payload=recorded[0][1]
    assert payload["ground_with_memory"] is True
    assert payload["context_turns"]==["Supplier A settles monthly"]
    assert payload["source_id"]=="conversation:2"
    assert "candidates" not in payload


def test_sdk_defaults_unchanged_and_never_forces_model_grounding():
    recorded=[]
    def responder(req):
        recorded.append(json.loads(req.content))
        return httpx.Response(200,json={"accepted":True})
    with MemoryClient("http://localhost","key",transport=httpx.MockTransport(responder)) as client:
        client.process(text="hello",source_id="message:1")
    assert "ground_with_memory" not in recorded[0]


def test_sdk_rejects_candidates_plus_grounding_before_network():
    def should_not_call(_):
        raise AssertionError("SDK should reject invalid combination locally")
    with MemoryClient("http://localhost","key",
                      transport=httpx.MockTransport(should_not_call)) as client:
        with pytest.raises(ValueError,match="cannot be combined"):
            client.process(text="hello",source_id="s",
                           candidates=[],ground_with_memory=True)
