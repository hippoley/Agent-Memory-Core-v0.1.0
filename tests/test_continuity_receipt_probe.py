"""Pure stdlib verifier conformance; no AMC runtime import required."""
import importlib.util
import json
import subprocess
import sys
import shutil
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


def test_effect_count_is_bounded_even_for_direct_python_import():
    b = valid()
    b["effects"]["items"] = [
        {**valid()["effects"]["items"][0], "effect_key": f"effect-{i}"}
        for i in range(probe.MAX_EFFECT_ITEMS + 1)
    ]
    assert probe.verify(b)["reasons"] == ["EFFECT_LIST_TOO_LARGE"]


def test_cli_rejects_oversized_receipt_without_loading_all_data(tmp_path):
    file = tmp_path / "oversized.json"
    file.write_bytes(b"x" * (probe.MAX_BUNDLE_BYTES + 1))
    run = subprocess.run(
        [sys.executable, str(SCRIPT), str(file)],
        capture_output=True, text=True, check=False)
    assert run.returncode == 1
    assert json.loads(run.stdout)["reasons"] == ["INPUT_TOO_LARGE"]


def test_cli_deeply_nested_json_is_failure_not_traceback(tmp_path):
    file = tmp_path / "deep.json"
    file.write_text("[" * 1200 + "0" + "]" * 1200, encoding="utf-8")
    run = subprocess.run(
        [sys.executable, str(SCRIPT), str(file)],
        capture_output=True, text=True, check=False)
    assert run.returncode == 1
    assert json.loads(run.stdout)["reasons"] == ["INVALID_INPUT_FILE"]
    assert "Traceback" not in run.stderr


def test_copied_single_script_runs_in_isolated_python_without_amc(tmp_path):
    # Independent consumption: script and JSON, no project deps or source tree.
    copied = tmp_path / "verify.py"
    shutil.copyfile(SCRIPT, copied)
    for state, expected_code, expected_decision in [
        ("completed", 0, "PASS"), ("running", 2, "UNKNOWN")]:
        bundle = valid()
        bundle["task"]["status"] = state
        receipt = tmp_path / "receipt.json"
        receipt.write_text(json.dumps(bundle), encoding="utf-8")
        run = subprocess.run(
            [sys.executable, "-I", "-S", str(copied), str(receipt)],
            cwd=tmp_path, capture_output=True, text=True, check=False)
        assert run.returncode == expected_code, run.stderr
        assert json.loads(run.stdout)["decision"] == expected_decision
        assert "Traceback" not in run.stderr


def test_public_vector_manifest_runs_without_amc_runtime():
    runner = ROOT / "tools" / "run_continuity_vectors.py"
    done = subprocess.run([sys.executable, "-I", "-S", str(runner)],
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    result = json.loads(done.stdout)
    assert result["cases"] >= 10
    assert result["all_passed"] is True
    assert result["failed"] == 0
    assert result["physical_execution_verified"] is False
    assert result["third_party_adoption_verified"] is False


def test_vector_runner_rejects_corrupted_expectations(tmp_path):
    original = ROOT / "conformance" / "vectors" / "continuity-ledger-v1.json"
    document = json.loads(original.read_text(encoding="utf-8"))
    document["cases"][0]["expected"]["reason"] = "WRONG_REASON"
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(document), encoding="utf-8")
    runner = ROOT / "tools" / "run_continuity_vectors.py"
    done = subprocess.run(
        [sys.executable, "-I", "-S", str(runner), "--manifest", str(tampered)],
        capture_output=True, text=True, check=False)
    assert done.returncode == 1
    report = json.loads(done.stdout)
    assert report["failed"] == 1
    assert report["all_passed"] is False
