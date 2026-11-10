"""Evaluate the real loopback Ollama extractor against a judged Chinese corpus.

JSONL record: {"case_id":"...", "category":"negation|correction|coreference|entity_ambiguity|temporal|durable_preference|no_fact", "text":"...", "expected_facts":[{"subject_id":"...","predicate":"...","value":{...}}]}
Acceptance requires >=200 adjudicated cases, micro fact F1 >=0.90,
correction-family exact accuracy >=0.95, and zero extractor errors.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys
import time

REQUIRED_CATEGORIES={"negation","correction","coreference","entity_ambiguity",
                     "temporal","durable_preference","no_fact"}
CORRECTION_CATEGORIES={"negation","correction","coreference","entity_ambiguity","temporal"}

def canonical(fact):
    return json.dumps({k:fact.get(k) for k in
        ("subject_id","subject_name","predicate","value")},
        ensure_ascii=False,sort_keys=True,separators=(",",":"))

def score(rows):
    tp=fp=fn=exact=errors=0
    per=defaultdict(lambda:{"tp":0,"fp":0,"fn":0,"exact":0,"cases":0})
    correction_exact=correction_total=0
    latencies=[]
    for row in rows:
        expected={canonical(x) for x in row["expected_facts"]}
        predicted={canonical(x) for x in row["predicted_facts"]}
        a=len(expected & predicted);b=len(predicted-expected);c=len(expected-predicted)
        tp+=a;fp+=b;fn+=c
        matched=expected==predicted
        exact+=int(matched)
        category=row["category"]
        item=per[category]
        item["tp"]+=a;item["fp"]+=b;item["fn"]+=c
        item["exact"]+=int(matched);item["cases"]+=1
        if category in CORRECTION_CATEGORIES:
            correction_total+=1
            correction_exact+=int(matched)
        errors+=int(row["error"] is not None)
        latencies.append(row["latency_ms"])
    precision=tp/(tp+fp) if tp+fp else 0.0
    recall=tp/(tp+fn) if tp+fn else 0.0
    f1=2*precision*recall/(precision+recall) if precision+recall else 0.0
    for item in per.values():
        p=item["tp"]/(item["tp"]+item["fp"]) if item["tp"]+item["fp"] else 0
        r=item["tp"]/(item["tp"]+item["fn"]) if item["tp"]+item["fn"] else 0
        item["f1"]=round(2*p*r/(p+r) if p+r else 0,4)
        item["exact_match_accuracy"]=round(item["exact"]/item["cases"],4)
    ordered=sorted(latencies)
    def pct(q):
        return round(ordered[min(len(ordered)-1,int((len(ordered)-1)*q+0.999999))],2)
    return {"cases":len(rows),"micro_precision":round(precision,4),
        "micro_recall":round(recall,4),"micro_f1":round(f1,4),
        "exact_match_accuracy":round(exact/len(rows),4),
        "correction_accuracy":round(correction_exact/correction_total,4) if correction_total else 0,
        "correction_cases":correction_total,"latency_p50_ms":pct(.5),
        "latency_p95_ms":pct(.95),"extractor_errors":errors,
        "categories":dict(sorted(per.items()))}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--dataset",required=True)
    parser.add_argument("--output",default="memory-extractor-evaluation.json")
    parser.add_argument("--min-cases",type=int,default=200)
    args=parser.parse_args()
    model=os.getenv("MEMORY_LOCAL_OLLAMA_MODEL")
    if not model:
        parser.error("Set MEMORY_LOCAL_OLLAMA_MODEL to an installed local Ollama model")
    rows=[]
    for line_no,line in enumerate(Path(args.dataset).read_text(encoding="utf-8").splitlines(),1):
        if not line.strip():
            continue
        row=json.loads(line)
        if (not isinstance(row.get("text"),str) or not row["text"].strip()
                or not isinstance(row.get("expected_facts"),list)
                or row.get("category") not in REQUIRED_CATEGORIES):
            parser.error(f"Invalid judged corpus record at line {line_no}")
        rows.append(row)
    if len(rows)<args.min_cases:
        parser.error(f"Need at least {args.min_cases} adjudicated cases; found {len(rows)}")
    missing=REQUIRED_CATEGORIES-{r["category"] for r in rows}
    if missing:
        parser.error("Missing required categories: "+", ".join(sorted(missing)))
    from agent_memory_core.local_extractor import OllamaFactExtractor
    extractor=OllamaFactExtractor(model=model,endpoint=os.getenv(
        "MEMORY_LOCAL_OLLAMA_ENDPOINT","http://127.0.0.1:11434/api/generate"))
    results=[]
    for row in rows:
        started=time.perf_counter();error=None;predicted=[]
        try:
            predicted=extractor(row["text"])
        except Exception as exc:
            error=type(exc).__name__+": "+str(exc)
        results.append({**row,"predicted_facts":predicted,
            "latency_ms":(time.perf_counter()-started)*1000,"error":error})
    report=score(results)
    report.update({"model":model,"endpoint":"loopback Ollama",
        "evaluated_at":datetime.now(timezone.utc).isoformat(),
        "required_min_cases":args.min_cases,
        "required_categories":sorted(REQUIRED_CATEGORIES),
        "passed":report["micro_f1"]>=.90 and report["correction_accuracy"]>=.95
                 and report["extractor_errors"]==0})
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report["passed"] else 1

if __name__=="__main__":
    sys.exit(main())
