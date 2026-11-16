"""Re-score the unchanged 200 Chinese gold samples using the shipped rule extractor.

NO HTTP, PostgreSQL, LLM, search or synthetic outputs. This is raw extraction
quality only. In particular, E-pair database revisions are NOT claimed tested.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from agent_memory_core.rule_extractor import RuleFactExtractor

ROOT=Path(__file__).resolve().parents[2]
DEFAULT_GOLD=ROOT/"data/eval/gold_home_zh.jsonl"
ROOMS=("儿童房","卫生间","主卧","次卧","客厅","书房","厨房","卧室","阳台","玄关","浴室")


def _subject(text: str) -> str | None:
    matches=[(text.index(room),-len(room),room) for room in ROOMS if room in text]
    return min(matches)[2] if matches else None


def key(fact: dict) -> str:
    return json.dumps({
        "subject_id":fact.get("subject_id") or fact.get("subject_name"),
        "predicate":fact.get("predicate"),
        "value":fact.get("value"),
        "operation":fact.get("operation","UPSERT"),
    },ensure_ascii=False,sort_keys=True,separators=(",",":"))


def tally(expected: list[dict], actual: list[dict]) -> tuple[int,int,int]:
    g=Counter(key(x) for x in expected)
    a=Counter(key(x) for x in actual)
    return sum((g&a).values()),sum((a-g).values()),sum((g-a).values())


def prf(tp: int, fp: int, fn: int) -> dict:
    p=tp/(tp+fp) if tp+fp else 0.
    r=tp/(tp+fn) if tp+fn else 0.
    return {"tp":tp,"fp":fp,"fn":fn,"precision":round(p,6),"recall":round(r,6),
            "f1":round(2*p*r/(p+r),6) if p+r else 0.}


def evaluate(gold: list[dict], extractor) -> dict:
    totals=defaultdict(lambda: [0,0,0])
    details=[]
    oov_target_hit=0
    oov_total=0
    for row in gold:
        subject=_subject(row["text"])
        kind=row["kind"]
        target=[{"subject_id":subject,**g} for g in row["gold"]]
        got=extractor(row["text"])
        if not isinstance(got,list):
            raise ValueError(f"Extractor output is not a list: {row['id']}")
        tp,fp,fn=tally(target,got)
        for i,value in enumerate((tp,fp,fn)):
            totals[kind][i]+=value
        if kind=="D":
            oov_total+=1
            gkeys={key(x) for x in target}
            oov_target_hit+=bool(gkeys & {key(x) for x in got})
        details.append({
            "id":row["id"],"kind":kind,"text":row["text"],
            "tp":tp,"fp":fp,"fn":fn,
            "expected":target,
            "predicted":[{"subject_id":g.get("subject_id") or g.get("subject_name"),
                          "predicate":g.get("predicate"),"value":g.get("value"),
                          "operation":g.get("operation","UPSERT")} for g in got],
        })
    aggregate=[sum(totals[k][i] for k in ("A","B","C")) for i in range(3)]
    return {
        "n":len(gold),
        "scope":"RULE_EXTRACTION_ONLY",
        "extraction_abc":prf(*aggregate),
        "by_kind":{k:{"cases":sum(x["kind"]==k for x in details),"metrics":prf(*totals[k])}
                   for k in sorted(totals)},
        "oov_exact_coverage":{"hit":oov_target_hit,"total":oov_total,
                              "rate":round(oov_target_hit/oov_total,6) if oov_total else None},
        "revision":"NOT_RUN - E-pair candidate extraction is not a database revision test",
        "retrieval":"NOT_RUN",
        "answer":"NOT_RUN",
        "errors":[x for x in details if x["fp"] or x["fn"]],
        "details":details,
    }


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--gold",type=Path,default=DEFAULT_GOLD)
    parser.add_argument("--json-out",required=True,type=Path)
    args=parser.parse_args()
    raw=args.gold.read_bytes()
    rows=[json.loads(x) for x in raw.decode("utf-8").splitlines() if x.strip()]
    if len(rows)!=200 or len({x["id"] for x in rows})!=len(rows):
        raise ValueError("Expected exactly 200 uniquely identified fixed Chinese samples")
    report=evaluate(rows,RuleFactExtractor.from_preset("home-zh"))
    report["dataset_sha256"]=hashlib.sha256(raw).hexdigest()
    args.json_out.parent.mkdir(parents=True,exist_ok=True)
    args.json_out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in ("details","errors")},
                     ensure_ascii=False,indent=2))
    print(f"Detailed error records: {len(report['errors'])}; saved to {args.json_out}")


if __name__=="__main__":
    main()
