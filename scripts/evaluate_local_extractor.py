"""Run the Chinese extraction fixture set against a loopback local model."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from agent_memory_core.evaluation import score_case
from agent_memory_core.local_extractor import LocalOpenAICompatibleExtractor


def main(argv=None) -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--dataset",default="benchmarks/chinese_memory_eval.jsonl")
    parser.add_argument("--url",default=os.getenv("MEMORY_LOCAL_MODEL_URL"))
    parser.add_argument("--model",default=os.getenv("MEMORY_LOCAL_MODEL"))
    parser.add_argument("--timeout",type=float,default=30)
    args=parser.parse_args(argv)
    if not args.url or not args.model:
        parser.error("Set --url/--model or MEMORY_LOCAL_MODEL_URL/MEMORY_LOCAL_MODEL")
    extractor=LocalOpenAICompatibleExtractor(
        base_url=args.url,model=args.model,timeout=args.timeout)
    totals={"true_positive":0,"false_positive":0,"false_negative":0}
    polarity_correct=0
    polarity_total=0
    for line in Path(args.dataset).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case=json.loads(line)
        predicted=extractor.extract_with_context(case["text"],case.get("context",[]))
        score=score_case(case["expected"],predicted)
        for key in totals:
            totals[key]+=score[key]
        if score["polarity_accuracy"] is not None:
            polarity_correct+=score["polarity_accuracy"]*score["polarity_comparable"]
            polarity_total+=score["polarity_comparable"]
        print(json.dumps({"id":case["id"],"score":score,"predicted":predicted},
                         ensure_ascii=False))
    tp,fp,fn=(totals[k] for k in ("true_positive","false_positive","false_negative"))
    precision=tp/(tp+fp) if tp+fp else 0.0
    recall=tp/(tp+fn) if tp+fn else 0.0
    f1=2*precision*recall/(precision+recall) if precision+recall else 0.0
    print(json.dumps({
        "aggregate":{"tp":tp,"fp":fp,"fn":fn,"precision":precision,
                     "recall":recall,"f1":f1,
                     "polarity_accuracy":polarity_correct/polarity_total if polarity_total else None},
        "dataset":args.dataset,"model":args.model,
    },ensure_ascii=False,indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
