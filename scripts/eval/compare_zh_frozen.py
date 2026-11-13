"""Compare two complete frozen reports; refuse apples-to-oranges data."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


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
    args=p.parse_args()
    a=json.loads(args.a.read_text(encoding="utf-8"))
    b=json.loads(args.b.read_text(encoding="utf-8"))
    result=compare(a,b)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
