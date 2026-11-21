"""US-C01: no fabricated green status from AMC/provider ledger comparison."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "benchmarks" / "handoff" / "effect_reconciliation.py"
spec = importlib.util.spec_from_file_location("handoff_effect_reconciliation", path)
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)


REQUEST = {
    "effect_key": "supplier-approval-request",
    "request_id": "handoff-01:supplier-approval-request",
    "payload": {"subject": "supplier-01", "period": "quarterly"},
}
DIGEST = hashlib.sha256(module.canonical(REQUEST).encode()).hexdigest()
RECORD = {
    "effect_key": REQUEST["effect_key"],
    "request": REQUEST, "request_sha256": DIGEST, "status": "requested",
}
RECEIPT = {"status": "committed", **REQUEST}


def check(record=RECORD, receipt=RECEIPT, **kw):
    return module.assess_effect(
        recorded=record, request_id=REQUEST["request_id"],
        effect_key=REQUEST["effect_key"],
        expected_payload=REQUEST["payload"], receipt=receipt, **kw)


def test_external_receipt_does_not_automatically_complete_amc_unknown():
    r = check()
    assert r.classification == "MATCHED_RECEIPT_AMC_UNRESOLVED"
    assert r.matches_recorded_intent
    assert not r.safe_for_automatic_completion
    for state in ("unknown", "executing", "reconciling"):
        assert check({**RECORD, "status": state}).classification == (
            "MATCHED_RECEIPT_AMC_UNRESOLVED")


def test_absence_transport_failure_and_conflicts_fail_closed():
    assert check(None).classification == "AMC_INTENT_MISSING"
    assert check({**RECORD, "effect_key": "payment"}).classification == "AMC_EFFECT_KEY_CONFLICT"
    assert check({**RECORD, "request_sha256": "0" * 64}).classification == "AMC_REQUEST_CONFLICT"
    assert check(receipt=None).classification == "PROVIDER_RECEIPT_ABSENT"
    assert check(provider_lookup_error=True).classification == "PROVIDER_LOOKUP_UNKNOWN"
    assert check(receipt={**RECEIPT, "payload": {"period": "monthly"}}).classification == (
        "PROVIDER_RECEIPT_CONFLICT")
    assert check(receipt={**RECEIPT, "request_id": "other"}).classification == (
        "PROVIDER_RECEIPT_CONFLICT")


def test_even_two_committed_ledgers_are_only_read_only_consistency():
    r = check({**RECORD, "status": "committed"})
    assert r.classification == "RECEIPTS_CONSISTENT_READ_ONLY"
    assert r.matches_recorded_intent
    assert not r.safe_for_automatic_completion


def test_read_only_effect_lookup_is_tenant_scoped():
    class FakeConn:
        def execute(self, sql, params):
            assert "JOIN memory_durable_tasks t" in sql
            assert "t.tenant_id=%s" in sql
            assert "SELECT" in sql and "UPDATE " not in sql
            assert params == ("tenant-01", "123e4567-e89b-12d3-a456-426614174000",
                              "supplier-approval-request")
            return self

        def fetchone(self):
            return (REQUEST["effect_key"], REQUEST, DIGEST, "unknown")

    record = module.load_tenant_effect(
        FakeConn(), tenant_id="tenant-01",
        task_id="123e4567-e89b-12d3-a456-426614174000",
        effect_key="supplier-approval-request",
    )
    assert record["request_sha256"] == DIGEST
    assert check(record).classification == "MATCHED_RECEIPT_AMC_UNRESOLVED"
