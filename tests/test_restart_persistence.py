"""Regression tests for the public SDK restart receipt contract."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_restart_persistence", ROOT / "scripts" / "check_restart_persistence.py"
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class FakeClient:
    def __init__(self, *, lose_history=False, lose_current=False, fail_supersession=False):
        self.lose_history = lose_history
        self.lose_current = lose_current
        self.fail_supersession = fail_supersession

    def recall(self, subject):
        return {"items": [] if self.lose_current else [
            {"id": "new", "subject_id": subject, "value": {"version": 2}}
        ]}

    def list_memories(self, *, subject_id, include_superseded):
        assert include_superseded is True
        return {"items": [
            {"id": "old", "subject_id": subject_id, "value": {"version": 1},
             "superseded_at": None if self.fail_supersession else "2026-11-20T00:00:00Z"},
            *([] if self.lose_history else [{
                "id": "new", "subject_id": subject_id, "value": {"version": 2},
                "superseded_at": None, "forgotten_at": None,
            }]),
        ]}


RECEIPT = {
    "tenant_id": "demo-home", "subject_id": "acceptance-1",
    "original_id": "old", "revised_id": "new",
}


def test_can_verify_persistent_revision_through_public_sdk():
    probe.verify_receipt(FakeClient(), RECEIPT)


@pytest.mark.parametrize("failure", ["lose_history", "lose_current", "fail_supersession"])
def test_missing_persistent_state_fails_closed(failure):
    with pytest.raises(AssertionError):
        probe.verify_receipt(FakeClient(**{failure: True}), RECEIPT)


def test_invalid_or_incomplete_receipt_fails_closed():
    with pytest.raises(AssertionError):
        probe.verify_receipt(FakeClient(), {"subject_id": "acceptance-1"})


def test_capture_refuses_to_replace_existing_receipt(tmp_path, monkeypatch):
    receipt = tmp_path / "receipt.json"
    receipt.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(probe, "RECEIPT", receipt)
    with pytest.raises(RuntimeError, match="Existing"):
        probe.capture(FakeClient(), "demo-home")


@pytest.mark.parametrize("bad_current", [
    {"memory-api": "a" * 64, "memory-db": "b" * 64},
    {"memory-api": "c" * 64, "memory-db": "b" * 64},
    {"memory-api": "a" * 64, "memory-db": "d" * 64},
    {},
])
def test_restart_requires_both_container_instances_to_change(bad_current):
    baseline = {"container_ids": {
        "memory-api": "a" * 64, "memory-db": "b" * 64,
    }}
    with pytest.raises(AssertionError):
        probe.assert_restarted(baseline, bad_current)


def test_restart_requires_original_container_evidence():
    with pytest.raises(AssertionError, match="no container identity baseline"):
        probe.assert_restarted(RECEIPT, {
            "memory-api": "c" * 64, "memory-db": "d" * 64,
        })


def test_restart_accepts_changed_api_and_database_instances():
    probe.assert_restarted({
        "container_ids": {"memory-api": "a" * 64, "memory-db": "b" * 64},
    }, {"memory-api": "c" * 64, "memory-db": "d" * 64})


def test_container_identity_readback_rejects_missing_service(monkeypatch):
    class Result:
        stdout = ""
    monkeypatch.setattr(probe.subprocess, "run", lambda *args, **kwargs: Result())
    with pytest.raises(AssertionError, match="missing or invalid"):
        probe.compose_container_ids()
