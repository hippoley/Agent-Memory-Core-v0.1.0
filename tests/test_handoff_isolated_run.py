"""Deterministic policy checks plus real TCP/SQLite provider regression."""
import importlib.util
import json
import multiprocessing
from pathlib import Path
import socket
import sqlite3
import time
from urllib.request import Request, urlopen

import pytest

path = Path(__file__).resolve().parents[1] / "benchmarks" / "handoff" / "isolated_run.py"
spec = importlib.util.spec_from_file_location("isolated_run", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def pack(period="quarterly", approval=True):
    return {
        "claims": [
            {"predicate": "payment_period", "value": {"payment_period": period}},
            {"predicate": "requires_approval", "value": {"requires_approval": approval}},
        ],
        "omitted_claim_ids": [], "unresolved_effects": [],
    }


def test_policy_requires_unambiguous_complete_claims():
    assert module.decide(pack()) == {"payment_period": "quarterly", "requires_approval": True}
    invalid = [
        pack(approval=False), pack(period="yearly"), {"claims": []},
        {**pack(), "omitted_claim_ids": ["lost"]},
        {**pack(), "unresolved_effects": [{"status": "unknown"}]},
        {**pack(), "claims": pack()["claims"] * 2},
    ]
    for bad in invalid:
        with pytest.raises(ValueError):
            module.decide(bad)


def test_provider_deduplicates_execution_and_journals_retries(tmp_path):
    db = str(tmp_path / "provider.sqlite")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = multiprocessing.Process(
        target=module.serve_provider, args=(db, "127.0.0.1", port))
    process.start()
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.1):
                    break
            except OSError:
                time.sleep(.02)
        else:
            pytest.fail("provider did not become ready")
        for _ in range(3):
            payload = json.dumps({
                "effect_key": "supplier-approval-request",
                "request_id": "handoff-01:approval",
                "payload": {"period": "quarterly"},
            }).encode()
            with urlopen(Request(
                f"http://127.0.0.1:{port}/effects", data=payload,
                headers={"Content-Type": "application/json"}), timeout=3) as response:
                assert response.status == 200
        # Reusing the same idempotency key for different semantics must fail.
        different = json.dumps({
            "effect_key": "supplier-payment",
            "request_id": "handoff-01:approval",
            "payload": {"period": "quarterly"},
        }).encode()
        from urllib.error import HTTPError
        with pytest.raises(HTTPError) as error:
            urlopen(Request(
                f"http://127.0.0.1:{port}/effects", data=different,
                headers={"Content-Type": "application/json"}), timeout=3)
        assert error.value.code == 409
        with sqlite3.connect(db) as conn:
            events = conn.execute(
                "SELECT executed FROM effect_events ORDER BY id").fetchall()
        assert events == [(1,), (0,), (0,)]
    finally:
        process.terminate()
        process.join(timeout=5)


def test_observer_is_handoff_scoped_and_never_claims_reconciled(tmp_path, monkeypatch):
    db = str(tmp_path / "events.sqlite")
    with sqlite3.connect(db) as conn:
        module.initialize(conn)
        conn.execute(
            "INSERT INTO effect_events(effect_key,request_id,executed,payload_json) VALUES (?,?,?,?)",
            ("supplier-approval-request", "handoff-1:approval", 1, "{}"))
        conn.execute(
            "INSERT INTO effect_events(effect_key,request_id,executed,payload_json) VALUES (?,?,?,?)",
            ("supplier-payment", "another-handoff:payment", 1, "{}"))
        conn.commit()

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def recall(self, _):
            return {"items": [
                {"predicate": "handoff_decision", "source_id": "handoff:handoff-1",
                 "value": {"payment_period": "quarterly", "requires_approval": True}},
                {"predicate": "handoff_decision", "source_id": "handoff:another-handoff",
                 "value": {"payment_period": "monthly", "requires_approval": False}},
            ]}

    monkeypatch.setattr(module, "amc_client", lambda: Client())
    trace = module.observe("case-1", "supplier-1", "handoff-1", db, 12, 25)
    assert trace["observed_facts"]["payment_period"] == "quarterly"
    assert trace["observed_effects"] == [{"effect_key": "supplier-approval-request"}]
    assert trace["observation_complete"] is False
    assert trace["unresolved_unknown"] is True
