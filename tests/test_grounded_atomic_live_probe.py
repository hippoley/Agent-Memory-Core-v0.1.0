"""Guardrails and history verdict for opt-in real grounded model probe.

These tests do NOT run a model. The live probe needs a separately configured
local model and disposable PostgreSQL-backed API to qualify its User Story.
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/eval/probe_grounded_atomic_live.py"
spec = importlib.util.spec_from_file_location("grounded_atomic_probe", PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:8000", "http://example.org:8000",
    "http://user:secret@localhost:8000", "file:///tmp/memory",
    "http://localhost:8000/?key=secret",
])
def test_probe_rejects_non_disposable_network_targets(url):
    with pytest.raises(ValueError):
        probe.require_loopback(url)


def test_probe_requires_disposable_consent_before_any_network_calls():
    args = SimpleNamespace(ack_disposable=False, key="A" * 32,
                           extractor_model="local-model", base_url="http://127.0.0.1:8767")
    with pytest.raises(ValueError, match="ack-disposable"):
        probe.run(args)


def test_history_requires_old_claim_retired_and_one_new_current():
    rows = [
        {"id": "old", "subject_id": "supplier-1",
         "predicate": "settlement_period", "value": {"months": 1},
         "superseded_at": "2026-11-17T01:00:00Z"},
        {"id": "new", "subject_id": "supplier-1",
         "predicate": "settlement_period", "value": {"months": 3},
         "superseded_at": None},
    ]
    result = probe.assess_history(rows, "old", "supplier-1")
    assert result == {"original_retired": True, "quarterly_active": True,
                      "no_wrong_current": True, "active_quarterly_id": "new"}


def test_history_fails_when_old_value_remains_current_or_duplicate_active():
    rows = [
        {"id": "old", "subject_id": "supplier-1",
         "predicate": "settlement_period", "value": {"months": 1},
         "superseded_at": None},
        {"id": "new", "subject_id": "supplier-1",
         "predicate": "settlement_period", "value": {"months": 3},
         "superseded_at": None},
    ]
    result = probe.assess_history(rows, "old", "supplier-1")
    assert not result["original_retired"]
    assert not result["no_wrong_current"]
    rows[0]["superseded_at"] = "2026-11-17T01:00:00Z"
    rows.append({**rows[1], "id": "duplicate"})
    assert not probe.assess_history(rows, "old", "supplier-1")["quarterly_active"]
