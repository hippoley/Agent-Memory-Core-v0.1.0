"""Score actual paired cross-session Agent handoffs; never generate predictions.

Gold is consumed only during scoring. Exact-answer success is a proxy, not
evidence of successful tool execution or independent model verification.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

ARMS = ("no_memory", "summary", "amc")


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def load_gold(path: Path):
    raw = path.read_bytes()
    gold = json.loads(raw)
    if not isinstance(gold, list) or not gold:
        raise ValueError("Gold must be a nonempty JSON array")
    ids = []
    for task in gold:
        if (not isinstance(task, dict) or not isinstance(task.get("id"), str)
                or not task["id"] or not isinstance(task.get("accepted_answers"), list)
                or not task["accepted_answers"]
                or not all(isinstance(x, str) and x for x in task["accepted_answers"])):
            raise ValueError("Gold task requires an ID and accepted answers")
        ids.append(task["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate task ID in gold")
    return gold, hashlib.sha256(raw).hexdigest()


def validate_arm(payload, arm, digest, ids):
    if not isinstance(payload, dict) or payload.get("arm") != arm:
        raise ValueError("Wrong experiment arm")
    if payload.get("dataset_sha256") != digest:
        raise ValueError("Frozen dataset hash mismatch")
    if payload.get("capture_type") != "real_agent_future_task":
        raise ValueError("Cannot score fixture or unverified capture as real Agent run")
    identity = payload.get("model_runtime_identity")
    if (not isinstance(identity, dict) or identity.get("verified") is not True
            or not isinstance(identity.get("response_model"), str)
            or not identity["response_model"]):
        raise ValueError("Observed model identity missing")
    config = payload.get("configuration")
    if (not isinstance(config, dict)
            or config.get("case_isolation") != "fresh_tenant_per_task"
            or type(config.get("max_context_tokens")) is not int
            or config["max_context_tokens"] <= 0
            or config.get("original_dialogue_visible") is not False):
        raise ValueError("Experiment isolation or context budget invalid")
    if not isinstance(payload.get("run_id"), str) or not payload["run_id"]:
        raise ValueError("Missing independent run identifier")
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != len(ids):
        raise ValueError("Incomplete task set")
    answers = {}
    for case in cases:
        if (not isinstance(case, dict) or set(case) != {"id", "answer"}
                or not isinstance(case.get("id"), str)
                or not isinstance(case.get("answer"), str)
                or case["id"] in answers):
            raise ValueError("Duplicate, missing or invalid answer")
        answers[case["id"]] = case["answer"]
    if set(answers) != set(ids):
        raise ValueError("Task IDs differ from gold")
    return answers


def score(gold, digest, arms, *, min_cases=30):
    if type(min_cases) is not int or min_cases < 1:
        raise ValueError("min_cases must be a positive integer")
    if set(arms) != set(ARMS):
        raise ValueError("Three-arm comparison required")
    ids = [x["id"] for x in gold]
    predictions = {name: validate_arm(arms[name], name, digest, ids) for name in ARMS}
    if len({arms[name]["run_id"] for name in ARMS}) != len(ARMS):
        raise ValueError("Repeated capture run ID")
    if len({canonical(arms[name]["model_runtime_identity"]) for name in ARMS}) != 1:
        raise ValueError("Observed models differ between arms")
    if len({canonical(arms[name]["configuration"]) for name in ARMS}) != 1:
        raise ValueError("Experiment conditions differ between arms")
    counts = dict.fromkeys(ARMS, 0)
    details = []
    for task in gold:
        record = {"id": task["id"]}
        accepted = {x.strip() for x in task["accepted_answers"]}
        for name in ARMS:
            success = predictions[name][task["id"]].strip() in accepted
            counts[name] += int(success)
            record[name] = success
        details.append(record)
    rates = {name: round(counts[name] / len(gold), 6) for name in ARMS}
    delta = round(rates["amc"]-rates["summary"], 6)
    return {"dataset_sha256": digest, "n": len(gold), "accuracy": rates,
            "amc_minus_summary": delta,
            "amc_minus_no_memory": round(rates["amc"]-rates["no_memory"], 6),
            "paired_cases": details,
            "release_gate": {
                "sufficient_sample": len(gold) >= min_cases,
                "amc_at_least_85pct": rates["amc"] >= .85,
                "amc_over_summary_10pp": delta >= .10,
                "eligible": len(gold) >= min_cases and rates["amc"] >= .85 and delta >= .10},
            "limitations": "Exact-answer proxy only; operator-reported capture metadata is not cryptographic attestation, proof of Agent tool execution, or causal evidence."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", required=True, type=Path)
    for name in ARMS:
        parser.add_argument("--"+name.replace("_", "-"), required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--min-cases", type=int, default=30)
    args = parser.parse_args()
    gold, digest = load_gold(args.gold)
    arms = {name: json.loads(getattr(args, name).read_text(encoding="utf-8"))
            for name in ARMS}
    result = score(gold, digest, arms, min_cases=args.min_cases)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k != "paired_cases"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
