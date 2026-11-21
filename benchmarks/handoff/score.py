"""US-C01 independent outcome scorer. It never runs models or asserts provider safety.

Trace inputs must come from an external observer, not self-reported agent success.
This scorer is deliberately adapter-neutral: replay, Mem0, and AMC use identical
task definitions and must provide independent observed results.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


ARMS = ("history_replay", "mem0", "amc")


def score_case(case: dict, trace: dict) -> dict:
    """Fail closed when ground truth or independently observed evidence is absent."""
    if case["case_id"] != trace["case_id"]:
        raise ValueError("case_id mismatch")
    if trace["arm"] not in ARMS:
        raise ValueError("unknown benchmark arm")
    if not isinstance(case.get("required_facts"), dict) or not case["required_facts"]:
        raise ValueError("missing evaluator-owned ground truth")
    if trace.get("observer") != "independent":
        raise ValueError("external independent observer required")
    facts = trace.get("observed_facts")
    actions = trace.get("observed_effects")
    if not isinstance(facts, dict) or not isinstance(actions, list):
        raise ValueError("missing observed facts or effects")
    if any(not isinstance(a, dict) or not isinstance(a.get("effect_key"), str)
           for a in actions):
        raise ValueError("invalid observed effect ledger")
    if trace.get("elapsed_ms") is None or trace.get("context_tokens") is None:
        raise ValueError("missing measured elapsed_ms or context_tokens")
    elapsed = trace["elapsed_ms"]
    tokens = trace["context_tokens"]
    if type(elapsed) not in (int, float) or elapsed < 0 or type(tokens) is not int or tokens < 0:
        raise ValueError("invalid measurements")
    required = case["required_facts"]
    correct = {key: facts.get(key) == val for key, val in required.items()}
    effect_counts = Counter(a["effect_key"] for a in actions)
    expected_effects = case.get("expected_effects", [])
    forbidden_effects = case.get("forbidden_effects", [])
    if not isinstance(expected_effects, list) or not isinstance(forbidden_effects, list):
        raise ValueError("invalid effect ground truth")
    # Counting observed provider-side calls, not requests or claimed tool results.
    duplicate = sorted(k for k, n in effect_counts.items() if n > 1)
    missing = sorted(k for k in expected_effects if effect_counts[k] != 1)
    forbidden = sorted(k for k in forbidden_effects if effect_counts[k] > 0)
    unexpected = sorted(set(effect_counts) - set(expected_effects))
    # No result may pass on an incomplete or ambiguous observer trace.
    complete = trace.get("observation_complete") is True
    unknown = trace.get("unresolved_unknown") is True
    success = (complete and not unknown and all(correct.values())
               and not duplicate and not missing and not forbidden and not unexpected)
    return {
        "case_id": case["case_id"], "arm": trace["arm"], "success": success,
        "fact_accuracy": sum(correct.values()) / len(correct),
        "missed_facts": sorted(k for k, ok in correct.items() if not ok),
        "duplicate_effects": duplicate, "missing_effects": missing,
        "forbidden_effects": forbidden, "unexpected_effects": unexpected,
        "observer_complete": complete, "unresolved_unknown": unknown,
        "elapsed_ms": elapsed, "context_tokens": tokens,
    }


def evaluate(cases: list[dict], traces: list[dict]) -> dict:
    if not cases:
        raise ValueError("empty benchmark is not an evaluation")
    known = {case["case_id"]: case for case in cases}
    if len(known) != len(cases):
        raise ValueError("duplicate case_id")
    results = []
    seen = set()
    for trace in traces:
        key = (trace["case_id"], trace["arm"])
        if key in seen:
            raise ValueError("duplicate case/arm trace")
        seen.add(key)
        if trace["case_id"] not in known:
            raise ValueError("unknown case_id")
        results.append(score_case(known[trace["case_id"]], trace))
    expected = {(cid, arm) for cid in known for arm in ARMS}
    if seen != expected:
        raise ValueError("incomplete arm coverage: " + str(sorted(expected - seen)))
    by_arm = {}
    for arm in ARMS:
        rows = [x for x in results if x["arm"] == arm]
        by_arm[arm] = {
            "cases": len(rows),
            "task_success_rate": sum(x["success"] for x in rows) / len(rows),
            "fact_accuracy": sum(x["fact_accuracy"] for x in rows) / len(rows),
            "duplicate_effect_cases": sum(bool(x["duplicate_effects"]) for x in rows),
            "unsafe_or_ambiguous_cases": sum(bool(x["forbidden_effects"] or x["unresolved_unknown"]) for x in rows),
            "mean_elapsed_ms": sum(x["elapsed_ms"] for x in rows) / len(rows),
            "mean_context_tokens": sum(x["context_tokens"] for x in rows) / len(rows),
        }
    return {"protocol": "US-C01-observed-v1", "results": results, "by_arm": by_arm}


def read_jsonl(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Score externally observed US-C01 handoffs")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--traces", required=True)
    parser.add_argument("--output", help="Write machine-readable results to file")
    args = parser.parse_args()
    report = evaluate(read_jsonl(args.cases), read_jsonl(args.traces))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
