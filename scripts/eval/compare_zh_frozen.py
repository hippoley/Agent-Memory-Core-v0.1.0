"""Compare two complete frozen reports; refuse apples-to-oranges data."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def require_context_window_pair(a: dict, b: dict) -> None:
    """Refuse a 1-vs-3 experiment if a second uncontrolled variable changed."""
    config_a = a.get("configuration")
    config_b = b.get("configuration")
    if not isinstance(config_a, dict) or not isinstance(config_b, dict):
        raise ValueError("Both reports need complete experiment configurations")
    if config_a.get("context_enabled") is not True or config_b.get("context_enabled") is not True:
        raise ValueError("Context-window comparison requires enabled context on both sides")
    windows = (config_a.get("context_window"), config_b.get("context_window"))
    if windows != (1, 3):
        raise ValueError("Context-window comparison requires window 1 then window 3")
    if (config_a.get("case_isolation") != "fresh_tenant_per_story"
            or config_b.get("case_isolation") != "fresh_tenant_per_story"):
        raise ValueError("Strict context comparison requires fresh tenant per story")
    for cfg in (config_a, config_b):
        identity = cfg.get("extractor_runtime_identity")
        if not isinstance(identity, dict) or identity.get("verified") is not True:
            raise ValueError("Strict context pair requires actual extractor runtime identity")
        if not identity.get("response_model"):
            raise ValueError("Strict context pair requires a response model identity")
    if config_a["extractor_runtime_identity"] != config_b["extractor_runtime_identity"]:
        raise ValueError("Strict context pair changed extractor runtime identity")
    for cfg in (config_a, config_b):
        answer_identity = cfg.get("answer_runtime_identity")
        if (not isinstance(answer_identity, dict)
                or answer_identity.get("verified") is not True
                or not answer_identity.get("response_model")):
            raise ValueError("Strict context pair requires observed answer runtime identity")
    if config_a["answer_runtime_identity"] != config_b["answer_runtime_identity"]:
        raise ValueError("Strict context pair changed answer runtime identity")
    for key in ("extractor", "aliases_enabled", "answer_model"):
        if config_a.get(key) != config_b.get(key):
            raise ValueError("Context-window experiment differs in " + key)
    # Additional pinned runtime metadata is allowed, but only identical values.
    ignore = {"name", "context_window"}
    if ({k:v for k,v in config_a.items() if k not in ignore} !=
            {k:v for k,v in config_b.items() if k not in ignore}):
        raise ValueError("Context-window experiment changed another configuration field")


def compare(a: dict, b: dict) -> dict:
    if a.get("dataset_sha256") != b.get("dataset_sha256") or a.get("cases") != b.get("cases"):
        raise ValueError("Cannot compare reports from different frozen datasets")
    if a.get("configuration", {}).get("name") == b.get("configuration", {}).get("name"):
        raise ValueError("Configuration names must be distinct")
    metrics = {
        "extraction_f1": ("extraction","f1"),
        "revision_final_state_exact": ("revision","exact_final_state_accuracy"),
        "retrieval_recall_at_5": ("retrieval","recall_at_5"),
        "retrieval_mrr_at_5": ("retrieval","mrr_at_5"),
        "answer_exact_accepted": ("answer","exact_accepted_accuracy"),
        "answer_abstention": ("answer","abstention_accuracy"),
        "end_to_end_exact_story": ("end_to_end","exact_story_accuracy"),
    }
    values={}
    for name,(section,key) in metrics.items():
        old=a[section][key];new=b[section][key]
        values[name]={"before":old,"after":new,
                      "delta":round(new-old,6) if isinstance(old,(float,int)) and isinstance(new,(float,int)) else None}
    return {
      "dataset_sha256":a["dataset_sha256"],
      "n":a["cases"],
      "a":a["configuration"],
      "b":b["configuration"],
      "metrics":values,
      "warning":"Paired descriptive deltas only; this small corpus cannot establish statistical significance."
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--a",required=True,type=Path)
    p.add_argument("--b",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    p.add_argument("--strict-context-pair",action="store_true",
                   help="Require same model/settings, with only window 1 vs 3 changed")
    args=p.parse_args()
    a=json.loads(args.a.read_text(encoding="utf-8"))
    b=json.loads(args.b.read_text(encoding="utf-8"))
    if args.strict_context_pair:
        require_context_window_pair(a,b)
    result=compare(a,b)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
