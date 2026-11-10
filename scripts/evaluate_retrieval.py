"""Measure real HTTP retrieval against a human-adjudicated JSONL corpus.

Record schema: {"query":"...", "relevant_memory_ids":["uuid", ...],
                "embedding":[...optional...], "model_id":"...optional..."}
Requires a running isolated local service and 200+ judged queries. The script
never seeds data or mutates canonical memory; it only issues search requests.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys
import time

def score_query(relevant_ids, ranked_ids):
    relevant=set(relevant_ids)
    if not relevant:
        raise ValueError("At least one relevant memory ID is required")
    ranked=list(ranked_ids)
    recall_at_5=len(relevant.intersection(ranked[:5]))/len(relevant)
    first_rank=next((rank for rank,memory_id in enumerate(ranked[:10],1)
                     if memory_id in relevant),None)
    reciprocal_rank=1/first_rank if first_rank else 0.0
    return recall_at_5,reciprocal_rank,first_rank


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--dataset",required=True)
    parser.add_argument("--base-url",default="http://127.0.0.1:8000")
    parser.add_argument("--output",default="memory-retrieval-evaluation.json")
    parser.add_argument("--min-cases",type=int,default=200)
    args=parser.parse_args()
    from urllib.parse import urlsplit
    parsed=urlsplit(args.base_url)
    if parsed.scheme!="http" or parsed.hostname not in ("127.0.0.1","localhost","::1"):
        parser.error("Only a loopback test service is accepted")
    key=os.getenv("MEMORY_API_KEY")
    if not key:
        parser.error("MEMORY_API_KEY is required")
    rows=[json.loads(line) for line in Path(args.dataset).read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows)<args.min_cases:
        parser.error(f"Need {args.min_cases} judged queries; found {len(rows)}")
    from agent_memory_core.sdk import MemoryClient
    recall5=0.0
    reciprocal=[]
    latencies=[]
    failures=0
    details=[]
    with MemoryClient(args.base_url,key,timeout=30) as client:
        for index,row in enumerate(rows):
            expected=set(row["relevant_memory_ids"])
            if not expected or not isinstance(row.get("query"),str):
                parser.error(f"Invalid query record at index {index}")
            started=time.perf_counter()
            try:
                result=client.search(query=row["query"],limit=10,
                    embedding=row.get("embedding"),model_id=row.get("model_id"))
                ids=[hit["memory_id"] for hit in result["items"]]
                recalled5,rr,rank=score_query(expected,ids)
                recall5+=recalled5
                reciprocal.append(rr)
                failures+=0
                details.append({"index":index,"recall_at_5":recalled5,"first_relevant_rank":rank,
                    "channels_used":result.get("channels_used",[]),
                    "degraded_channels":result.get("degraded_channels",{})})
            except Exception as exc:
                failures+=1
                reciprocal.append(0.0)
                details.append({"index":index,"error":type(exc).__name__+": "+str(exc)})
            latencies.append((time.perf_counter()-started)*1000)
    ordered=sorted(latencies)
    def pct(q):
        return round(ordered[min(len(ordered)-1,int((len(ordered)-1)*q+0.999999))],2)
    report={"cases":len(rows),"recall_at_5":round(recall5/len(rows),4),
        "mrr_at_10":round(sum(reciprocal)/len(rows),4),
        "latency_p50_ms":pct(.5),"latency_p95_ms":pct(.95),
        "request_failures":failures,"details":details,
        "evaluated_at":datetime.now(timezone.utc).isoformat(),
        "passed":recall5/len(rows)>=.90 and sum(reciprocal)/len(rows)>=.85 and failures==0}
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report["passed"] else 1

if __name__=="__main__":
    sys.exit(main())
