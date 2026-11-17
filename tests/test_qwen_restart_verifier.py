"""Fail-closed unit tests of the independent, read-only restart verifier.

Fixtures here test only verifier policy; the opt-in Actions workflow is the
separate, real-process and real-PostgreSQL runtime acceptance.
"""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

EVAL = Path(__file__).resolve().parents[1] / "scripts/eval"
sys.path.insert(0, str(EVAL))
spec = importlib.util.spec_from_file_location(
    "qwen_independent_restart_verifier", EVAL / "verify_qwen_postrestart.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def primary():
    return {"passed": True, "subject": "supplier-test-case",
            "initial_id": "original-uuid",
            "checks": {"active_quarterly_id": "new-uuid"}}


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.calls = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def request(self, method, path, **kwargs):
        self.calls.append((method, path))
        data = {
            "/v1/ready": {"status": "ready"},
            "/v1/memories": {"items": [
                {"id": "new-uuid", "value": {"months": 3},
                 "superseded_at": None}]},
            "/v1/search": {"items": [
                {"memory_id": "new-uuid", "subject_id": "supplier-test-case",
                 "predicate": "settlement_period", "value": {"months": 3}}]},
        }
        if path == "/v1/memories" and kwargs["params"].get("include_superseded"):
            data["/v1/memories"] = {"items": [
                {"id": "original-uuid", "value": {"months": 1},
                 "superseded_at": "2026-11-17T00:00:00+00:00"},
                {"id": "new-uuid", "value": {"months": 3},
                 "superseded_at": None}]}
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: data[path])


def test_independent_new_process_readback_accepts_correct_history(monkeypatch):
    monkeypatch.setattr(verifier.httpx, "Client", FakeClient)
    report = verifier.verify("http://127.0.0.1:8767", "k" * 32, primary())
    assert report["passed"] is True
    assert all(report["checks"].values())


def test_independent_readback_rejects_bad_primary_before_network(monkeypatch):
    class NoClient:
        def __init__(self, *a, **kw):
            raise AssertionError("Should not open HTTP without primary proof")
    monkeypatch.setattr(verifier.httpx, "Client", NoClient)
    with pytest.raises(ValueError, match="Primary"):
        verifier.verify("http://127.0.0.1:8767", "k" * 32, {**primary(), "passed": False})
    with pytest.raises(ValueError):
        verifier.verify("https://example.org", "k" * 32, primary())


def test_independent_readback_rejects_stale_claim_after_restart(monkeypatch):
    class Stale(FakeClient):
        def request(self, method, path, **kwargs):
            result = super().request(method, path, **kwargs)
            if path == "/v1/search":
                return SimpleNamespace(raise_for_status=lambda: None,
                                       json=lambda: {"items": [
                                           {"memory_id": "original-uuid"}]})
            return result
    monkeypatch.setattr(verifier.httpx, "Client", Stale)
    report = verifier.verify("http://localhost:8767", "k" * 32, primary())
    assert report["passed"] is False
    assert report["checks"]["old_search_excluded"] is False


def test_restart_workflow_is_manual_on_main_and_requires_both_receipts():
    workflow = (Path(__file__).resolve().parents[1]
                / ".github/workflows/qwen-real-acceptance.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert "  push:" not in workflow
    assert "kill \"$(cat /tmp/memory-api-qwen.pid)\"" in workflow
    assert "verify_qwen_postrestart.py" in workflow
    assert 'out["restart_passed"]' in workflow
    assert 'out["postgres_restart_passed"] is True' in workflow
    assert "docker compose stop memory-db" in workflow
    assert "docker compose up -d --wait memory-db" in workflow
    assert "qwen-postgres-restart-readback.json" in workflow

def test_postgres_restart_scope_is_explicit_and_checked(monkeypatch):
    monkeypatch.setattr(verifier.httpx, "Client", FakeClient)
    report = verifier.verify("http://127.0.0.1:8767", "k" * 32,
                             primary(), scope="postgres-container-restart")
    assert report["passed"] is True
    assert report["scope"] == "postgres-container-restart"
    with pytest.raises(ValueError, match="scope"):
        verifier.verify("http://127.0.0.1:8767", "k" * 32,
                        primary(), scope="not-a-real-proof")
