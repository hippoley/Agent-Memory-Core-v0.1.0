"""Pure stdlib verifier conformance; no AMC runtime import required."""
import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "verify_continuity_receipt.py"
spec = importlib.util.spec_from_file_location("continuity_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def valid():
    task_id = "11111111-1111-4111-8111-111111111111"
    return {"task": {"task_id": task_id, "task_type": "continuity.effect.v1",
                     "status": "completed", "input": {
                         "required_effect_keys": ["set-bedroom-target-v1"]}},
            "effects": {"task_id": task_id, "items": [{
                "effect_key": "set-bedroom-target-v1", "request_sha256": "a" * 64,
                "status": "committed", "attempt_count": 1,
                "provider_reference_recorded": True,
                "physical_independence_verified": False,
                "causal_effect_verified": False}]}}


def test_pass_means_ledger_only_not_physical():
    outcome = probe.verify(valid())
    assert outcome["decision"] == "PASS"
    assert outcome["reasons"] == ["LEDGER_CONSISTENT_ONLY"]
    assert outcome["physical_effect_verified"] is False
    assert outcome["actor_authorization_verified"] is False


@pytest.mark.parametrize("change,decision,reason", [
    (lambda b: b["effects"].update(task_id="another"), "FAIL", "TASK_ID_MISMATCH"),
    (lambda b: b["task"]["input"].update(required_effect_keys=["different"]),
     "FAIL", "MISSING_REQUIRED_EFFECT"),
    (lambda b: b["effects"]["items"].clear(), "FAIL", "MISSING_REQUIRED_EFFECT"),
    (lambda b: b["effects"]["items"][0].update(status="unknown"),
     "FAIL", "REQUIRED_EFFECT_NOT_COMMITTED"),
    (lambda b: b["task"].update(status="running"), "UNKNOWN", "TASK_NOT_COMPLETED"),
    (lambda b: (b["task"].update(status="running"),
                b["effects"]["items"][0].update(status="unknown")),
     "UNKNOWN", "UNRESOLVED_EXTERNAL_EFFECT"),
    (lambda b: b["effects"]["items"][0].update(request_sha256="bad"),
     "FAIL", "INVALID_EFFECT_HASH"),
    (lambda b: b["effects"]["items"][0].update(attempt_count=0),
     "FAIL", "COMMITTED_WITHOUT_PROVIDER_REFERENCE"),
    (lambda b: b["effects"]["items"][0].update(provider_reference_recorded=False),
     "FAIL", "COMMITTED_WITHOUT_PROVIDER_REFERENCE"),
    (lambda b: b["effects"]["items"][0].update(causal_effect_verified=True),
     "FAIL", "UNSUPPORTED_PHYSICAL_ATTESTATION"),
    (lambda b: b["task"].update(task_type="unrelated.task"),
     "UNKNOWN", "UNSUPPORTED_TASK_PROFILE"),
])
def test_adversarial_decisions(change, decision, reason):
    b = valid()
    change(b)
    out = probe.verify(b)
    assert (out["decision"], out["reasons"][0]) == (decision, reason)


def test_duplicate_effect_rejected():
    b = valid()
    b["effects"]["items"].append(deepcopy(b["effects"]["items"][0]))
    assert probe.verify(b)["reasons"] == ["INVALID_OR_DUPLICATE_EFFECT_KEY"]


def test_unknown_must_not_be_reported_as_success():
    b = valid()
    b["effects"]["items"][0]["status"] = "unknown"
    assert probe.verify(b)["decision"] != "PASS"


def test_cli_exit_codes_are_machine_consumable(tmp_path):
    p = tmp_path / "bundle.json"
    for state, expected_code in [("completed", 0), ("running", 2)]:
        b = valid()
        b["task"]["status"] = state
        p.write_text(json.dumps(b), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)],
            capture_output=True, text=True, check=False)
        assert result.returncode == expected_code
        assert json.loads(result.stdout)["decision"] == (
            "PASS" if state == "completed" else "UNKNOWN")


def test_strict_creator_profile_is_a_ledger_profile_not_auth_attestation():
    b = valid()
    b["task"]["task_type"] = "continuity.authorized-effect.v1"
    result = probe.verify(b)
    assert result["decision"] == "PASS"
    assert result["actor_authorization_verified"] is False
    assert result["physical_effect_verified"] is False
    assert result["snapshot_authenticity_verified"] is False


@pytest.mark.parametrize("field_path,bad_value,reason", [
    (("task", "task_type"), [], "INVALID_TASK_PROFILE"),
    (("task", "status"), [], "INVALID_TASK_STATUS"),
    (("effects", "items", 0, "status"), [], "INVALID_EFFECT_STATUS"),
    (("effects", "items", 0, "status"), {"x": 1}, "INVALID_EFFECT_STATUS"),
])
def test_malformed_unhashable_enums_return_fail_not_uncaught_exception(
        field_path, bad_value, reason):
    b = valid()
    obj = b
    for key in field_path[:-1]:
        obj = obj[key]
    obj[field_path[-1]] = bad_value
    assert probe.verify(b)["reasons"] == [reason]


def test_cli_rejects_duplicate_json_keys(tmp_path):
    p = tmp_path / "duplicate.json"
    # Silent last-key wins would permit contradictory task status claims.
    p.write_text('{"task":{"status":"running","status":"completed"},"effects":{}}',
                 encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(p)],
        capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert json.loads(result.stdout)["reasons"] == ["INVALID_INPUT_FILE"]


def test_cli_rejects_nonstandard_json_nan(tmp_path):
    p = tmp_path / "nan.json"
    p.write_text('{"task":NaN,"effects":{}}', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(p)],
        capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert json.loads(result.stdout)["reasons"] == ["INVALID_INPUT_FILE"]
