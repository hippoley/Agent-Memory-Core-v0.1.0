"""Cardinality is operator policy, never model output.

The dashboard and the demo script both lean on this: a room may hold many
windows (explicit `many`), but at most one current temperature (`one` by
default), and an extractor that asserts otherwise is rejected rather than
trusted.
"""
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from agent_memory_core.api import create_app
from agent_memory_core.processing import MemoryProcessor

API_KEY = "p" * 32


class PolicyStore:
    def __init__(self):
        self.policies = {}
        self.claims = []

    def close(self):
        pass

    def list_predicate_policies(self, *, tenant_id):
        return [{"predicate": p, "cardinality": c, "updated_at": None}
                for (t, p), c in sorted(self.policies.items()) if t == tenant_id]

    def set_predicate_cardinality(self, *, tenant_id, predicate, cardinality):
        if cardinality not in ("one", "many"):
            raise ValueError("Invalid tenant predicate policy")
        if (tenant_id, predicate) in self.policies and self.policies[(tenant_id, predicate)] != cardinality:
            raise ValueError("Cannot change cardinality for an existing predicate without reconciliation")
        self.policies[(tenant_id, predicate)] = cardinality

    def predicate_cardinality(self, *, tenant_id, predicate):
        return self.policies.get((tenant_id, predicate), "one")

    # processing surface
    def recall(self, *, tenant_id, subject_id, predicate=None, valid_at=None, known_at=None):
        return [c for c in self.claims
                if c["tenant_id"] == tenant_id and c["subject_id"] == subject_id
                and (predicate is None or c["predicate"] == predicate)
                and c["superseded_at"] is None]

    def remember(self, **kwargs):
        old = kwargs.get("replaces_id")
        if old:
            for claim in self.claims:
                if claim["id"] == old:
                    claim["superseded_at"] = datetime.now(timezone.utc)
        mid = f"00000000-0000-4000-8000-{len(self.claims) + 1:012d}"
        self.claims.append({"id": mid, "superseded_at": None, **kwargs})
        return mid

    def link_claim_source(self, **kwargs):
        return True

    def resolve_entity(self, *, tenant_id, name):
        return name


@pytest.fixture()
def store():
    return PolicyStore()


@pytest.fixture()
def client(store):
    app = create_app(api_key=API_KEY, tenant_id="demo", store_factory=lambda: store)
    return TestClient(app, headers={"Authorization": f"Bearer {API_KEY}"})


def test_unlisted_predicate_defaults_to_single_valued(client):
    body = client.get("/v1/predicate-policies").json()
    assert body["default"] == "one"
    assert body["items"] == []


def test_set_and_list_policy(client):
    response = client.put("/v1/predicate-policies/has_window", json={"cardinality": "many"})
    assert response.status_code == 200
    assert response.json()["cardinality"] == "many"
    listed = client.get("/v1/predicate-policies").json()["items"]
    assert listed == [{"predicate": "has_window", "cardinality": "many", "updated_at": None}]


def test_invalid_predicate_and_cardinality_are_rejected(client):
    assert client.put("/v1/predicate-policies/房间温度", json={"cardinality": "many"}).status_code == 422
    assert client.put("/v1/predicate-policies/x", json={"cardinality": "two"}).status_code == 422
    assert client.put("/v1/predicate-policies/x", json={"cardinality": "many", "extra": 1}).status_code == 422


def test_changing_policy_with_existing_claims_conflicts(client, store):
    store.policies[("demo", "temperature")] = "one"
    with pytest.raises(ValueError):
        store.set_predicate_cardinality(tenant_id="demo", predicate="temperature",
                                        cardinality="many")
    assert client.put("/v1/predicate-policies/temperature",
                      json={"cardinality": "many"}).status_code == 409


def fact(subject, predicate, value, cardinality=None, confidence=0.9):
    candidate = {"subject_id": subject, "predicate": predicate,
                 "value": value, "confidence": confidence}
    if cardinality is not None:
        candidate["cardinality"] = cardinality
    return candidate


def test_model_cannot_upgrade_cardinality(store):
    """A candidate asserting `many` for a single-valued predicate is rejected."""
    processor = MemoryProcessor(store, confidence_threshold=0.5,
                                entity_resolver=lambda tenant_id, name: name)
    report = processor.process(
        tenant_id="demo", text="卧室温度 26 度",
        source_id="demo:1",
        extractor=lambda _text: [fact("bedroom", "temperature", {"celsius": 26}, "many")],
    )
    assert report.created == []
    assert report.rejected == 1
    assert store.claims == []


def test_many_allows_two_windows_but_one_still_blocks_two_temperatures(store):
    processor = MemoryProcessor(store, confidence_threshold=0.5,
                                entity_resolver=lambda tenant_id, name: name)
    store.set_predicate_cardinality(tenant_id="demo", predicate="has_window",
                                    cardinality="many")
    processor.process(tenant_id="demo", text="卧室有落地窗", source_id="demo:1",
                      extractor=lambda _t: [fact("bedroom", "has_window", {"side": "south"})])
    processor.process(tenant_id="demo", text="卧室还有一扇小窗", source_id="demo:2",
                      extractor=lambda _t: [fact("bedroom", "has_window", {"side": "north"})])
    assert len([c for c in store.claims if c["predicate"] == "has_window"]) == 2

    processor.process(tenant_id="demo", text="卧室 26 度", source_id="demo:3",
                      extractor=lambda _t: [fact("bedroom", "temperature", {"celsius": 26})])
    report = processor.process(tenant_id="demo", text="卧室 18 度", source_id="demo:4",
                               extractor=lambda _t: [fact("bedroom", "temperature", {"celsius": 18})])
    # single-valued: the second write cannot coexist, it must go through a correction
    assert report.created == [] or report.replaced
    active = [c for c in store.claims
              if c["predicate"] == "temperature" and c["superseded_at"] is None]
    assert len(active) == 1
