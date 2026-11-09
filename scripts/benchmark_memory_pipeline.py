"""Measure concurrent canonical writes, FTS projection throughput and query latency.

Use a dedicated disposable tenant: this script writes persistent benchmark facts.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import statistics
import time



def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--dsn",default=os.getenv("MEMORY_DSN"),required=False)
    parser.add_argument("--tenant-id",required=True,
                        help="Use a dedicated tenant; benchmark rows persist.")
    parser.add_argument("--count",type=int,default=100)
    parser.add_argument("--concurrency",type=int,default=8)
    parser.add_argument("--search-iterations",type=int,default=100)
    args=parser.parse_args(argv)
    if not args.dsn:
        parser.error("Set --dsn or MEMORY_DSN")
    if not (1<=args.count<=10000 and 1<=args.concurrency<=64
            and 1<=args.search_iterations<=10000):
        parser.error("count/search-iterations must be 1..10000; concurrency 1..64")

    from datetime import timezone
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.projection_jobs import ProjectionWorker
    from agent_memory_core.fts import project_claim_to_fts, search_fts
    from agent_memory_core.benchmarking import percentile

    def write_one(index):
        db=PostgresMemory(args.dsn)
        try:
            now=db.current_time()
            result=db.apply_candidate(
                tenant_id=args.tenant_id,
                subject_id=f"benchmark-subject-{index:06d}",
                predicate="benchmark_marker",
                value={"batch":args.tenant_id,"sequence":index,"tag":"benchmark"},
                source_id=f"benchmark-source-{index:06d}",
                valid_from=now,
            )
            return result
        finally:
            db.close()

    start=time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        writes=list(pool.map(write_one,range(args.count)))
    write_seconds=time.perf_counter()-start

    db=PostgresMemory(args.dsn)
    try:
        projection_start=time.perf_counter()
        projection_worker=ProjectionWorker(db.conn)
        projected=0
        while projected < args.count:
            batch=projection_worker.consume(
                projection_name="fts",tenant_id=args.tenant_id,
                handler=project_claim_to_fts,limit=min(100,args.count-projected))
            if batch==0:
                break
            projected+=batch
        projection_seconds=time.perf_counter()-projection_start

        latencies=[]
        for _ in range(args.search_iterations):
            before=time.perf_counter()
            search_fts(db.conn,tenant_id=args.tenant_id,
                       query="benchmark_marker",limit=10)
            latencies.append((time.perf_counter()-before)*1000)
        stats=ProjectionWorker(db.conn).stats(
            projection_name="fts",tenant_id=args.tenant_id)
    finally:
        db.close()

    report={
        "tenant_id":args.tenant_id,
        "writes":{"count":len(writes),"seconds":write_seconds,
                  "ops_per_second":len(writes)/write_seconds if write_seconds else None,
                  "created":sum(x["status"]=="created" for x in writes)},
        "fts_projection":{"processed":projected,"seconds":projection_seconds,
                          "jobs_per_second":projected/projection_seconds if projection_seconds else None,
                          "queue":stats},
        "fts_search_ms":{"count":len(latencies),"p50":percentile(latencies,0.50),
                         "p95":percentile(latencies,0.95),
                         "mean":statistics.fmean(latencies) if latencies else None},
        "limitations":["single-process benchmark runner",
                       "FTS only; vector and AGE projection excluded",
                       "not a 100-agent production load test"],
    }
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
