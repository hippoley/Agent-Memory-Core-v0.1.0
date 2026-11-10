"""Opt-in HTTP load acceptance: 100 concurrent Agents, write/read roundtrips.

Designed to run against an isolated local test deployment. Mutates the DB.
No implicit production testing; `--allow-write` is mandatory.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from uuid import uuid4
import argparse
import json
import os
import sys
import time


def percentile(values, q):
    if not values:
        return None
    ordered=sorted(values)
    index=max(0,min(len(ordered)-1,int((len(ordered)-1)*q+0.999999)))
    return round(ordered[index],3)


def summarize(records):
    groups={name:[record[name] for record in records if record.get(name) is not None]
            for name in ("write_ms","read_ms")}
    return {
        "requests":len(records)*2,
        "transactions":len(records),
        "failures":sum(not r["ok"] for r in records),
        "success_rate":sum(r["ok"] for r in records)/len(records) if records else 0,
        "write_p50_ms":percentile(groups["write_ms"],0.5),
        "write_p95_ms":percentile(groups["write_ms"],0.95),
        "read_p50_ms":percentile(groups["read_ms"],0.5),
        "read_p95_ms":percentile(groups["read_ms"],0.95),
    }


def run(base_url, api_key, agents, rounds):
    from agent_memory_core.sdk import MemoryClient
    def actor(index):
        results=[]
        with MemoryClient(base_url,api_key,timeout=30.0) as client:
            for n in range(rounds):
                subject=f"load-{index}-{n}-{uuid4().hex}"
                now=datetime.now(timezone.utc).isoformat()
                t0=time.perf_counter()
                try:
                    created=client.remember(
                        subject_id=subject,predicate="load_probe",
                        value={"agent":index,"round":n},
                        source_id=f"load-run:{index}:{n}",valid_from=now)
                    write_ms=(time.perf_counter()-t0)*1000
                    t1=time.perf_counter()
                    recalled=client.recall(subject,predicate="load_probe")
                    read_ms=(time.perf_counter()-t1)*1000
                    ids={x["id"] for x in recalled["items"]}
                    results.append({"ok":created["id"] in ids,
                                    "write_ms":write_ms,"read_ms":read_ms})
                except Exception as exc:
                    results.append({"ok":False,"write_ms":None,
                                    "read_ms":None,"error":type(exc).__name__})
        return results

    result=[]
    with ThreadPoolExecutor(max_workers=agents) as pool:
        futures=[pool.submit(actor,index) for index in range(agents)]
        for future in as_completed(futures):
            result.extend(future.result())
    return summarize(result)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--base-url",default="http://127.0.0.1:8000")
    parser.add_argument("--agents",type=int,default=100)
    parser.add_argument("--rounds",type=int,default=100)
    parser.add_argument("--allow-write",action="store_true")
    parser.add_argument("--output",default="memory-load-results.json")
    parser.add_argument("--write-p95-ms",type=float,default=800)
    parser.add_argument("--read-p95-ms",type=float,default=500)
    args=parser.parse_args()
    if not args.allow_write:
        parser.error("This benchmark writes persistent data: pass --allow-write on an isolated test tenant")
    if not 1 <= args.agents <= 1000 or not 1 <= args.rounds <= 10000:
        parser.error("agents and rounds out of supported range")
    from urllib.parse import urlsplit
    parsed=urlsplit(args.base_url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1","localhost","::1"):
        parser.error("Only a local test gateway may be benchmarked")
    key=os.getenv("MEMORY_API_KEY")
    if not key:
        parser.error("MEMORY_API_KEY must be set")
    report=run(args.base_url,key,args.agents,args.rounds)
    report.update({"agents":args.agents,"rounds_per_agent":args.rounds,
                   "write_p95_target_ms":args.write_p95_ms,
                   "read_p95_target_ms":args.read_p95_ms})
    report["passed"]=(
        report["success_rate"]>=0.999
        and report["write_p95_ms"] is not None
        and report["read_p95_ms"] is not None
        and report["write_p95_ms"]<=args.write_p95_ms
        and report["read_p95_ms"]<=args.read_p95_ms
    )
    Path(args.output).write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))
    return 0 if report["passed"] else 1


if __name__=="__main__":
    sys.exit(main())
