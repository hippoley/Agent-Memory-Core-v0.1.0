#!/usr/bin/env python3
"""Run public continuity ledger vectors against the copied, isolated CLI.

No AMC imports, database, network, pip, or pytest necessary:
  python tools/run_continuity_vectors.py
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def run(manifest_file: Path, verifier_file: Path) -> dict:
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("schema") != "amc.continuity-ledger-vectors.v1":
        raise ValueError("Unsupported vector manifest schema")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Nonempty cases array required")
    results = []
    ids = set()
    with tempfile.TemporaryDirectory(prefix="continuity-vectors-") as dirname:
        directory = Path(dirname)
        # The candidate verifier is executed as a separate, isolated script.
        # It is not imported into this harness or into an AMC runtime.
        for item in cases:
            if not isinstance(item, dict):
                raise ValueError("Vector case must be an object")
            case_id = item.get("id")
            if (not isinstance(case_id, str) or not case_id or
                    case_id in ids or not case_id.replace("-", "").isalnum()):
                raise ValueError("Invalid or duplicate vector ID")
            ids.add(case_id)
            expected = item.get("expected")
            if not isinstance(expected, dict):
                raise ValueError("Missing expected decision")
            expected_decision = expected.get("decision")
            expected_reason = expected.get("reason")
            expected_exit = expected.get("exit_code")
            if expected_decision not in ("PASS", "FAIL", "UNKNOWN"):
                raise ValueError("Invalid expected decision")
            if (not isinstance(expected_reason, str) or
                    type(expected_exit) is not int or expected_exit not in (0, 1, 2)):
                raise ValueError("Invalid expected reason or exit code")
            receipt = directory / "case.json"
            receipt.write_text(json.dumps(item.get("bundle"), ensure_ascii=False),
                               encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-I", "-S", str(verifier_file.resolve()), str(receipt)],
                cwd=directory, capture_output=True, text=True, check=False,
                timeout=15)
            try:
                output = json.loads(completed.stdout)
            except ValueError:
                output = {}
            ok = (
                completed.returncode == expected_exit
                and output.get("decision") == expected_decision
                and isinstance(output.get("reasons"), list)
                and output["reasons"][:1] == [expected_reason]
                and output.get("physical_effect_verified") is False
                and output.get("actor_authorization_verified") is False
                and output.get("snapshot_authenticity_verified") is False
            )
            results.append({
                "id": case_id, "passed": ok,
                "expected": expected_decision, "observed": output.get("decision"),
                "reason": output.get("reasons", [None])[0] if output.get("reasons") else None,
                "exit_code": completed.returncode,
                "stderr_present": bool(completed.stderr),
            })
    return {
        "schema": "amc.continuity-vector-report.v1",
        "manifest_version": manifest.get("version"),
        "cases": len(results),
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "all_passed": all(item["passed"] for item in results),
        "results": results,
        "physical_execution_verified": False,
        "third_party_adoption_verified": False,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        default=root / "conformance/vectors/continuity-ledger-v1.json")
    parser.add_argument("--verifier", type=Path,
                        default=root / "tools/verify_continuity_receipt.py")
    args = parser.parse_args()
    try:
        result = run(args.manifest, args.verifier)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"all_passed": False, "error": type(exc).__name__},
                         sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
