"""Run the REAL offline RuleFactExtractor against the frozen corpus.

This script measures extraction ONLY. It does not simulate a database, search
rankings, model answers, or revision outcome. For full E2E scores use
score_zh_frozen.py with actual outputs from a disposable HTTP deployment.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path

from agent_memory_core.rule_extractor import RuleFactExtractor

ROOT=Path(__file__).resolve().parents[2]
SCORER_PATH=ROOT/"scripts/eval/score_zh_frozen.py"


def _scorer():
    spec=importlib.util.spec_from_file_location("zh_scorer_for_diagnostic", SCORER_PATH)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(*, use_context: bool, preset: str="home-zh") -> dict:
    scorer=_scorer()
    gold,digest=scorer.read_gold(scorer.DEFAULT_GOLD)
    extractor=RuleFactExtractor.from_preset(preset)
    tp=fp=fn=0
    details=[]
    for case in gold:
        actual=[]
        for turn,text in enumerate(case["turns"]):
            if use_context and turn>0:
                previous=[case["turns"][turn-1]]
                facts=extractor.extract_with_context(text,previous)
            else:
                facts=extractor(text)
            actual.extend({**f,"turn":turn} for f in facts)
        t,p,n=scorer._counts(case["expected_events"],actual,event=True)
        tp+=t;fp+=p;fn+=n
        details.append({"id":case["id"],"category":case["category"],
                        "tp":t,"fp":p,"fn":n,"predicted_events":actual})
    return {
      "dataset_sha256":digest,"n":len(gold),
      "configuration":{"extractor":f"rule:{preset}","context_enabled":use_context},
      "extraction":scorer._prf(tp,fp,fn),
      "scope":"extraction_only",
      "other_stages":"NOT_RUN (revision/retrieval/answer cannot be inferred from rules)",
      "details":details
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--context",action="store_true")
    parser.add_argument("--out",required=True,type=Path)
    args=parser.parse_args()
    report=run(use_context=args.context)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k!="details"},ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
