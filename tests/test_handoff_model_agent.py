"""Model decision is a real-model-compatible interface; tests use mocks only."""
import json
import pytest
from agent_memory_core.handoff_model_agent import (
    choose_device_action, execute_model_guided_handoff, ModelDecisionError,
)
from agent_memory_core.handoff_effect_probe import HandoffEffectFailure

PAYLOAD = {"claims": [{"id": "m-1", "subject_id": "master-bedroom",
                      "predicate": "desired_temperature",
                      "value": {"temperature": 26}}]}


class Response:
    def __init__(self, data): self.data = data
    def raise_for_status(self): pass
    def json(self): return self.data


class Client:
    def __init__(self, data): self.data = data; self.sent = None
    def post(self, url, json): self.sent = json; return Response(self.data)


def result(args, *, model="qwen-local", name="set_device_property"):
    return {"model": model, "choices": [{"message": {
        "tool_calls": [{"type": "function", "function": {
            "name": name, "arguments": json.dumps(args)}}]}}]}


def args():
    return {"device_id": "master-bedroom", "property": "temperature", "value": 26}


def choose(data, *, payload=PAYLOAD, endpoint="http://127.0.0.1:8000/v1/chat/completions"):
    client = Client(data)
    plan = choose_device_action(
        accepted_payload=payload, device_id="master-bedroom",
        property_name="temperature", model="qwen-local",
        endpoint=endpoint, http_client=client)
    return plan, client


def test_model_proposes_exact_canonical_tool_action():
    plan, client = choose(result(args()))
    assert plan["value"] == 26 and plan["source_memory_id"] == "m-1"
    assert "26" in json.dumps(client.sent)


@pytest.mark.parametrize("bad", [
    result({**args(), "device_id": "other-room"}),
    result({**args(), "value": 25}),
    result(args(), model="changed"),
    result(args(), name="delete_all_memories"),
    {"model": "qwen-local", "choices": [{"message": {"content": "Success"}}]},
])
def test_unsafe_or_fabricated_model_action_fails_closed(bad):
    with pytest.raises(ModelDecisionError):
        choose(bad)


def test_empty_handoff_never_plans_effect():
    with pytest.raises(ModelDecisionError):
        choose(result(args()), payload={"claims": []})


def test_remote_model_endpoint_rejected():
    with pytest.raises(ModelDecisionError):
        choose(result(args()), endpoint="https://example.org/v1/chat/completions")


class MemoryStore:
    def __init__(self): self.rows = []
    def add(self, subject_id, predicate, value):
        row = {"id": str(len(self.rows)+1), "subject_id": subject_id,
               "predicate": predicate, "value": value}
        self.rows.append(row)
        return row


class MemoryAgent:
    def __init__(self, store): self.store = store
    def create_task(self, **kw): return {"task_id": "t-1"}
    def remember(self, *, subject_id, predicate, value, **kw):
        return self.store.add(subject_id, predicate, value)
    def create_handoff(self, **kw):
        self.store.packet = {"claims": [dict(x) for x in self.store.rows]}
        return {"handoff_id": "h-1"}
    def accept_handoff(self, id, **kw):
        return {"status": "accepted", "payload": self.store.packet}
    def recall(self, subject_id):
        return {"items": [x for x in self.store.rows if x["subject_id"] == subject_id]}


def test_model_guided_effect_requires_verified_readback():
    store, device = MemoryStore(), {"temperature": 24}
    receipt = execute_model_guided_handoff(
        agent_a=MemoryAgent(store), agent_b=MemoryAgent(store),
        device_id="master-bedroom", property_name="temperature",
        expected_value=26,
        actuator=lambda _, __, v: device.__setitem__("temperature", v),
        observer=lambda _, __: device["temperature"], model="qwen-local",
        endpoint="http://127.0.0.1:8000/v1/chat/completions",
        http_client=Client(result(args())))
    assert receipt.observed == 26
    assert store.rows[-1]["predicate"] == "verified_effect"


def test_tool_ack_without_state_change_cannot_write_receipt():
    store, device = MemoryStore(), {"temperature": 24}
    with pytest.raises(HandoffEffectFailure, match="readback"):
        execute_model_guided_handoff(
            agent_a=MemoryAgent(store), agent_b=MemoryAgent(store),
            device_id="master-bedroom", property_name="temperature",
            expected_value=26, actuator=lambda *_: {"success": True},
            observer=lambda _, __: device["temperature"], model="qwen-local",
            endpoint="http://127.0.0.1:8000/v1/chat/completions",
            http_client=Client(result(args())))
    assert len(store.rows) == 1
