"""Score a frozen Chinese memory benchmark with strict, non-LLM grading.

Usage:
  python scripts/eval/score_zh_frozen.py --predictions /tmp/rule.json --out /tmp/rule.report.json
  python scripts/eval/score_zh_frozen.py --predictions /tmp/model.json --out /tmp/model.report.json

Prediction JSON schema:
{
  "dataset_sha256": "<printed by --manifest>",
  "configuration": {"name": "rule-no-context", "extractor": "rule",
                    "context_enabled": false, "aliases_enabled": false,
                    "answer_model": "none"},
  "cases": [
    {"id": "zh01", "extracted": [{"turn": 0, "subject_id": "主卧",
                                  "predicate": "temperature_preference",
                                  "value": {"celsius": 24}, "operation": "UPSERT"}],
     "current": [{"subject_id": "主卧", "predicate": "temperature_preference",
                  "value": {"celsius": 26}}],
     "retrieved": [{"subject_id": "主卧", "predicate": "temperature_preference",
                    "value": {"celsius": 26}}],
     "answer": "26度"}
  ]
}

NO synthetic score is claimed. Prediction files must be captured from the
actual extraction/memory/search/answer pipelines; the evaluator never invents
missing predictions. For no-ground-truth retrieval, count clean negative
queries separately rather than assigning a misleading 100% Recall.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLD = ROOT / "data/eval/zh_multiturn_frozen_v1.jsonl"
STAGES = ("extracted", "current", "retrieved", "answer")
_CONFIG_FIELDS = ("name", "extractor", "context_enabled", "aliases_enabled", "answer_model")


def read_gold(path: Path) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    cases = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)) or not cases:
        raise ValueError("Gold corpus has duplicate IDs or is empty")
    required = {"id", "category", "turns", "expected_events",
                "expected_current", "query", "expected_retrieval", "accepted_answers"}
    for case in cases:
        missing = required - set(case)
        if missing or not case["accepted_answers"] or not case["turns"]:
            raise ValueError(f"Gold case {case['id']} missing fields or answers: {sorted(missing)}")
    return cases, digest


def _fact_key(fact: dict, *, event: bool = False) -> str:
    required = {"subject_id", "predicate", "value"}
    if event:
        required |= {"turn"}
    if required - set(fact):
        raise ValueError(f"Missing structured fact keys: {sorted(required-set(fact))}")
    if not isinstance(fact["value"], dict):
        raise ValueError("Fact value must be an object")
    payload = {k: fact[k] for k in required}
    if event:
        operation = fact.get("operation", "UPSERT")
        if operation not in ("UPSERT", "RETRACT"):
            raise ValueError("Unknown memory operation")
        payload["operation"] = operation
        # A retraction is scored as an operation targeting a specific fact
        # identity, not as equality between arbitrary marker values. The
        # original claim's prior value is validated by the revision stage.
        if operation == "RETRACT":
            payload.pop("value", None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _counts(gold: list[dict], predicted: list[dict], *, event: bool = False) -> tuple[int, int, int]:
    expected = Counter(_fact_key(x, event=event) for x in gold)
    actual = Counter(_fact_key(x, event=event) for x in predicted)
    return (sum((expected & actual).values()),
            sum((actual - expected).values()), sum((expected - actual).values()))


def _prf(tp: int, fp: int, fn: int) -> dict:
    # For a globally empty benchmark the score is undefined (not perfect).
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 6), "recall": round(recall, 6),
            "f1": round(2*precision*recall/(precision+recall), 6) if precision+recall else 0.0}


def _normalize_answer(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Answer must be a string")
    import re
    return re.sub(r"[\s。！？，,.!?]+", "", value.strip().lower()).replace("摄氏度", "度").replace("℃", "度")


def validate_predictions(payload: dict, cases: list[dict], digest: str) -> dict[str, dict]:
    if payload.get("dataset_sha256") != digest:
        raise ValueError("Dataset SHA-256 mismatch; use the exact frozen corpus")
    config = payload.get("configuration")
    if not isinstance(config, dict) or any(k not in config for k in _CONFIG_FIELDS):
        raise ValueError("Explicit experiment configuration required")
    if not isinstance(config["context_enabled"], bool) or not isinstance(config["aliases_enabled"], bool):
        raise ValueError("Ablation switches must be explicit booleans")
    predictions = payload.get("cases")
    if not isinstance(predictions, list):
        raise ValueError("Prediction cases must be an array")
    indexed = {}
    goldids = {case["id"] for case in cases}
    for pred in predictions:
        if not isinstance(pred, dict) or "id" not in pred:
            raise ValueError("Prediction requires case ID")
        case_id = pred["id"]
        if case_id in indexed or case_id not in goldids:
            raise ValueError(f"Duplicate or unknown prediction ID: {case_id}")
        missing = set(STAGES) - set(pred)
        if missing:
            raise ValueError(f"{case_id}: missing stage results {sorted(missing)}")
        for stage in STAGES[:3]:
            if not isinstance(pred[stage], list):
                raise ValueError(f"{case_id}: {stage} must be a list")
        if not isinstance(pred["answer"], str):
            raise ValueError(f"{case_id}: answer must be a string")
        indexed[case_id] = pred
    absent = goldids - set(indexed)
    if absent:
        raise ValueError(f"Predictions missing {len(absent)} cases: {sorted(absent)}")
    return indexed


def score(gold: list[dict], predictions: dict[str, dict], configuration: dict, digest: str) -> dict:
    extract_tp = extract_fp = extract_fn = 0
    correct_state = correct_answer = 0
    retrieve_tp = retrieve_fp = retrieve_fn = 0
    rr = 0.0
    positive_queries = clean_negative = negative_queries = 0
    answer_abstain_hits = answer_abstain_total = 0
    per_cat = defaultdict(lambda: {"n": 0, "state_ok": 0, "answer_ok": 0})
    details = []
    for case in gold:
        prediction = predictions[case["id"]]
        tp, fp, fn = _counts(case["expected_events"], prediction["extracted"], event=True)
        extract_tp += tp; extract_fp += fp; extract_fn += fn
        state_ok = Counter(_fact_key(x) for x in case["expected_current"]) == Counter(
            _fact_key(x) for x in prediction["current"])
        correct_state += state_ok
        goldret = {_fact_key(x) for x in case["expected_retrieval"]}
        gotret = [_fact_key(x) for x in prediction["retrieved"][:5]]
        hits = len(goldret & set(gotret))
        retrieve_tp += hits
        retrieve_fp += len(set(gotret) - goldret)
        retrieve_fn += len(goldret - set(gotret))
        if goldret:
            positive_queries += 1
            rr += max((1/i for i, key in enumerate(gotret, 1) if key in goldret), default=0)
        else:
            negative_queries += 1
            clean_negative += not gotret
        answer_ok = _normalize_answer(prediction["answer"]) in {
            _normalize_answer(a) for a in case["accepted_answers"]
        }
        correct_answer += answer_ok
        if case.get("expected_abstain", False):
            answer_abstain_total += 1
            answer_abstain_hits += answer_ok
        cat = per_cat[case["category"]]
        cat["n"] += 1
        cat["state_ok"] += bool(state_ok)
        cat["answer_ok"] += bool(answer_ok)
        details.append({"id":case["id"], "category":case["category"],
                        "extraction":{"tp":tp,"fp":fp,"fn":fn},
                        "state_exact":bool(state_ok),
                        "retrieval_hits":hits, "retrieval_gold":len(goldret),
                        "answer_exact":bool(answer_ok)})
    n=len(gold)
    return {
        "dataset_sha256":digest,
        "configuration":configuration,
        "cases":n,
        "extraction":_prf(extract_tp,extract_fp,extract_fn),
        "revision":{"exact_final_state_accuracy":round(correct_state/n,6),"correct":correct_state,"total":n},
        "retrieval":{"precision_recall":_prf(retrieve_tp,retrieve_fp,retrieve_fn),
                     "recall_at_5":round(retrieve_tp / (retrieve_tp+retrieve_fn),6) if retrieve_tp+retrieve_fn else None,
                     "mrr_at_5":round(rr/positive_queries,6) if positive_queries else None,
                     "positive_queries":positive_queries,
                     "clean_negative_queries":clean_negative,
                     "negative_queries":negative_queries},
        "answer":{"exact_accepted_accuracy":round(correct_answer/n,6),
                  "correct":correct_answer,"total":n,
                  "abstention_accuracy":round(answer_abstain_hits/answer_abstain_total,6) if answer_abstain_total else None,
                  "abstention_cases":answer_abstain_total},
        "by_category":dict(per_cat),
        "details":details,
        "grading_limitations":"Exact structured facts and predeclared normalized answer variants; not semantic model judging."
    }


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--gold",type=Path,default=DEFAULT_GOLD)
    parser.add_argument("--manifest",action="store_true",help="Print corpus hash and exit")
    parser.add_argument("--predictions",type=Path)
    parser.add_argument("--out",type=Path)
    args=parser.parse_args()
    gold,digest=read_gold(args.gold)
    if args.manifest:
        print(json.dumps({"dataset_sha256":digest,"cases":len(gold)},indent=2))
        return 0
    if args.predictions is None or args.out is None:
        parser.error("--predictions and --out required unless --manifest")
    payload=json.loads(args.predictions.read_text(encoding="utf-8"))
    predictions=validate_predictions(payload,gold,digest)
    report=score(gold,predictions,payload["configuration"],digest)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k!="details"},ensure_ascii=False,indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
