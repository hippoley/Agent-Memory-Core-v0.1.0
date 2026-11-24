"""Unit and real PostgreSQL HTTP integration probes for verified handoff."""
import os
from uuid import uuid4
import pytest
from agent_memory_core import handoff_effect_probe as probe


class Store:
    def __init__(self):
        self.rows = []
    def add(self, subject_id, predicate, value):
        row = {"id": str(len(self.rows)+1), "subject_id": subject_id,
               "predicate": predicate, "value": value}
        self.rows.append(row)
        return row


class Agent:
    def __init__(self, store):
        self.store = store
    def create_task(self, **kw):
        return {"task_id": "task-1"}
    def remember(self, *, subject_id, predicate, value, **kw):
        return self.store.add(subject_id, predicate, value)
    def create_handoff(self, **kw):
        self.store.packet = {"claims": [dict(row) for row in self.store.rows]}
        return {"handoff_id": "handoff-1"}
    def accept_handoff(self, handoff_id, **kw):
        return {"status": "accepted", "payload": self.store.packet}
    def recall(self, subject_id):
        return {"items": [dict(x) for x in self.store.rows
                          if x["subject_id"] == subject_id]}


def scenario(write=None, read=None):
    store, device = Store(), {"temperature": 24}
    def actuator(_, __, target):
        if write is not None:
            return write(device, target)
        device["temperature"] = target
    def observer(_, __):
        return read(device) if read is not None else device["temperature"]
    return store, dict(agent_a=Agent(store), agent_b=Agent(store),
                       device_id="room-1", property_name="temperature",
                       expected_value=26, actuator=actuator, observer=observer)


def test_verified_effect_persists_after_independent_readback():
    store, args = scenario()
    receipt = probe.execute_verified_handoff(**args)
    assert (receipt.before, receipt.observed) == (24, 26)
    assert store.rows[-1]["value"]["verification"] == "independent_readback"


def test_false_tool_acknowledgement_never_creates_receipt():
    store, args = scenario(write=lambda *_: {"success": True})
    with pytest.raises(probe.HandoffEffectFailure, match="readback"):
        probe.execute_verified_handoff(**args)
    assert len(store.rows) == 1


def test_preexisting_value_is_not_new_effect():
    store, args = scenario(read=lambda _: 26)
    with pytest.raises(probe.HandoffEffectFailure, match="already satisfied"):
        probe.execute_verified_handoff(**args)
    assert len(store.rows) == 1


def test_bad_accepted_packet_is_blocked_before_tool():
    store, args = scenario()
    class BadAgent(Agent):
        def accept_handoff(self, *a, **kw):
            return {"status": "accepted", "payload": {"claims": []}}
    args["agent_b"] = BadAgent(store)
    with pytest.raises(probe.HandoffEffectFailure, match="target intent"):
        probe.execute_verified_handoff(**args)
    assert len(store.rows) == 1


def test_same_client_must_not_impersonate_second_agent():
    _, args = scenario()
    args["agent_b"] = args["agent_a"]
    with pytest.raises(ValueError, match="independent"):
        probe.execute_verified_handoff(**args)


@pytest.mark.integration
def test_real_postgres_http_handoff_and_separate_readback():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Real PostgreSQL MEMORY_TEST_DSN required")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory

    db = PostgresMemory(dsn)
    db.migrate()
    db.close()
    tenant = "effect-" + uuid4().hex
    key = "effect-secret-" + uuid4().hex + uuid4().hex
    app = create_app(api_key=key, tenant_id=tenant,
                     store_factory=lambda: PostgresMemory(dsn))
    class HttpAgent:
        def __init__(self, client):
            self.client = client
        def request(self, method, route, **kw):
            response = self.client.request(method, route,
                headers={"Authorization": "Bearer " + key}, **kw)
            assert response.status_code < 400, response.text
            return response.json()
        def create_task(self, **kw):
            return self.request("POST", "/v1/tasks", json=kw)
        def remember(self, **kw):
            return self.request("POST", "/v1/memories", json=kw)
        def create_handoff(self, **kw):
            return self.request("POST", "/v1/handoffs", json=kw)
        def accept_handoff(self, id, **kw):
            return self.request("POST", "/v1/handoffs/" + id + "/accept", json=kw)
        def recall(self, subject_id):
            return self.request("GET", "/v1/memories/" + subject_id)
    state = {"temperature": 24}
    subject = "device-" + uuid4().hex
    with TestClient(app) as client_a, TestClient(app) as client_b:
        result = probe.execute_verified_handoff(
            agent_a=HttpAgent(client_a), agent_b=HttpAgent(client_b),
            device_id=subject, property_name="temperature", expected_value=26,
            actuator=lambda _id, _prop, value: state.__setitem__("temperature", value),
            observer=lambda _id, _prop: state["temperature"])
        assert (result.before, result.observed) == (24, 26)
        # Third HTTP session audits the persisted receipt.
        with TestClient(app) as client_c:
            rows = HttpAgent(client_c).recall(subject)["items"]
            assert any(c["id"] == result.verified_memory_id
                       and c["value"]["observed"] == 26 for c in rows)
