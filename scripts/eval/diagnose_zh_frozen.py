"""Explain all genuine frozen-memory benchmark mistakes stage by stage.

This reads captured predictions plus *frozen* gold; it does not generate
predictions, infer missing fields, or turn fixture values into model scores.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

try:
    from scripts.eval.score_zh_frozen import (
        DEFAULT_GOLD, read_gold, validate_predictions, _fact_key,
        _normalize_answer, STAGES,
    )
except ModuleNotFoundError:
    from score_zh_frozen import (
        DEFAULT_GOLD, read_gold, validate_predictions, _fact_key,
        _normalize_answer, STAGES,
    )


def _multiset_delta(gold: list[dict], actual: list[dict], *, event: bool = False) -> dict:
    expected = Counter(_fact_key(x, event=event) for x in gold)
    predicted = Counter(_fact_key(x, event=event) for x in actual)
    def expand(values):
        return [json.loads(x) for x, count in sorted(values.items()) for _ in range(count)]
    return {"missing": expand(expected - predicted), "unexpected": expand(predicted - expected)}


def diagnose(gold: list[dict], predictions: dict[str, dict]) -> dict:
    failures = []
    by_stage = {name: 0 for name in STAGES}
    for case in gold:
        result = predictions[case["id"]]
        issues = {}
        for stage, expected_key, is_event in (
            ("extracted", "expected_events", True),
            ("current", "expected_current", False),
            ("retrieved", "expected_retrieval", False),
        ):
            actual = result[stage][:5] if stage == "retrieved" else result[stage]
            delta = _multiset_delta(case[expected_key], actual, event=is_event)
            if delta["missing"] or delta["unexpected"]:
                issues[stage] = {
                    "error_type": "MISSING_OR_UNEXPECTED_FACT",
                    "expected": case[expected_key],
                    "actual": actual,
                    **delta,
                }
                by_stage[stage] += 1
        if _normalize_answer(result["answer"]) not in {
            _normalize_answer(x) for x in case["accepted_answers"]
        }:
            issues["answer"] = {
                "error_type": "UNACCEPTED_ANSWER",
                "expected": case["accepted_answers"],
                "actual": result["answer"],
                "expected_abstain": bool(case.get("expected_abstain", False)),
            }
            by_stage["answer"] += 1
        if issues:
            failures.append({
                "id": case["id"], "category": case["category"],
                "first_failure_stage": next(x for x in STAGES if x in issues),
                "failed_stages": [x for x in STAGES if x in issues],
                "issues": issues,
            })
    return {
        "total_cases": len(gold), "failed_cases": len(failures),
        "failures_by_stage": by_stage, "failures": failures,
        "warning": "Descriptive per-case differences only; not causal proof of downstream errors.",
    }


def require_real_capture(payload: dict) -> None:
    """Reject fixture or unverifiable captures at the strict diagnosis gate.

    These fields are observed runtime metadata from the capture, not proof of
    cryptographic model-weight identity. This validates the capture contract;
    external replay and hardware pinning remain independent requirements.
    """
    if not isinstance(payload, dict) or payload.get("capture_type") != "real_http_and_local_model":
        raise ValueError("Real capture attestation missing; fixture and oracle predictions cannot pass")
    config = payload.get("configuration")
    if not isinstance(config, dict):
        raise ValueError("Real capture requires complete configuration")
    extractor = config.get("extractor")
    answer = config.get("answer_model")
    if (not isinstance(extractor, str) or not extractor.strip()
            or extractor.startswith(("fixture", "rule"))
            or not isinstance(answer, str) or not answer.strip()
            or answer in ("fixture", "none")):
        raise ValueError("Real model identities required; rule/fixture is not a real-model run")
    for key in ("extractor_runtime_identity", "answer_runtime_identity"):
        identity = config.get(key)
        if (not isinstance(identity, dict)
                or identity.get("verified") is not True
                or not isinstance(identity.get("response_model"), str)
                or not identity["response_model"].strip()):
            raise ValueError("Real capture requires verified observed runtime identity for " + key)
    if config["extractor_runtime_identity"]["response_model"] != extractor:
        raise ValueError("Observed extractor runtime identity differs from declared model")
    if config["answer_runtime_identity"]["response_model"] != answer:
        raise ValueError("Observed answer runtime identity differs from declared model")
    attestation = payload.get("search_attestation")
    if (not isinstance(attestation, dict)
            or type(attestation.get("aliases_enabled")) is not bool
            or type(attestation.get("alias_count")) is not int
            or attestation["alias_count"] < 0
            or not isinstance(attestation.get("alias_sha256"), str)
            or len(attestation["alias_sha256"]) != 64
            or config.get("aliases_enabled") != attestation["aliases_enabled"]):
        raise ValueError("Authenticated search attestation is required and must match configuration")
    if config.get("case_isolation") != "fresh_tenant_per_story":
        raise ValueError("Strict real-model evidence requires a fresh tenant per story")
    cases = payload.get("cases")
    if (not isinstance(cases, list) or not cases
            or type(payload.get("isolated_tenant_count")) is not int
            or payload["isolated_tenant_count"] != len(cases)):
        raise ValueError("Real capture must attest one isolated tenant per story")
    extractor_calls = payload.get("extractor_runtime_call_count")
    answer_calls = payload.get("answer_runtime_call_count")
    if (type(extractor_calls) is not int or extractor_calls < len(cases)
            or type(answer_calls) is not int or answer_calls != len(cases)):
        raise ValueError("Real capture model call counts are incomplete")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    p.add_argument("--predictions", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--require-real-capture", action="store_true")
    args = p.parse_args()
    gold, digest = read_gold(args.gold)
    payload = json.loads(args.predictions.read_text(encoding="utf-8"))
    predictions = validate_predictions(payload, gold, digest)
    if args.require_real_capture:
        require_real_capture(payload)
    report = diagnose(gold, predictions)
    report.update({"dataset_sha256": digest, "configuration": payload["configuration"],
                   "capture_type": payload.get("capture_type", "unverified")})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k!="failures"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
