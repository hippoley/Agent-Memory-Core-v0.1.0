"""Reproducible PostgreSQL same-fact Agent concurrency benchmark.

100 logical Agent requests by default, with bounded database connections.
This is a benchmark runner, not a production throughput claim.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from time import perf_counter
from uuid import uuid4

from agent_memory_core.postgres import PostgresMemory
from agent_memory_core.processing import MemoryProcessor


def run(dsn: str, agents: int = 100, concurrency: int = 16) -> dict:
    if agents < 1 or not 1 <= concurrency <= agents:
        raise ValueError("agents and concurrency must be positive and bounded")
    tenant = "benchmark-" + uuid4().hex
    db = PostgresMemory(dsn)
    try:
        db.migrate()
    finally:
        db.close()

    def request(i: int):
        store = PostgresMemory(dsn)
        began = perf_counter()
        try:
            report = MemoryProcessor(store).process(
                tenant_id=tenant, text="quiet nights",
                source_id="agent-" + str(i),
                extractor=lambda _: [{"subject_id":"living-room",
                                       "predicate":"preference",
                                       "value":{"noise":"quiet"}}],
            )
            return ((perf_counter()-began)*1000, report)
        finally:
            store.close()

    started = perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        outcomes = list(pool.map(request, range(agents)))
    wall = perf_counter()-started
    durations = sorted(d for d, _ in outcomes)
    verify = PostgresMemory(dsn)
    try:
        current = verify.recall(tenant_id=tenant,subject_id="living-room",
                                predicate="preference")
        ids = [record["id"] for record in current]
        linked = verify.sources_for_memory(
            tenant_id=tenant,memory_id=ids[0]) if len(ids)==1 else []
        source_ids = {row["source_id"] for row in linked}
    finally:
        verify.close()
    created = sum(len(r.created) for _,r in outcomes)
    duplicates = sum(r.duplicates for _,r in outcomes)
    rejected = sum(r.rejected for _,r in outcomes)
    integrity = (len(ids)==1 and created==1 and duplicates==agents-1
                 and rejected==0 and len(source_ids)==agents)
    return {
        "agents":agents, "concurrency":concurrency, "tenant_id":tenant,
        "elapsed_seconds":round(wall,3),
        "throughput_requests_per_second":round(agents/wall,2),
        "latency_p50_ms":round(durations[math.ceil(.5*agents)-1],2),
        "latency_p95_ms":round(durations[math.ceil(.95*agents)-1],2),
        "created":created, "duplicates":duplicates,
        "rejected":rejected, "distinct_provenance_sources":len(source_ids),
        "active_claims":len(ids), "integrity_passed":integrity,
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents",type=int,default=100)
    parser.add_argument("--concurrency",type=int,default=16)
    args=parser.parse_args(argv)
    dsn=os.environ.get("MEMORY_TEST_DSN") or os.environ.get("MEMORY_DSN")
    if not dsn:
        parser.error("Set MEMORY_TEST_DSN or MEMORY_DSN")
    result=run(dsn,args.agents,args.concurrency)
    print(json.dumps(result,indent=2))
    return 0 if result["integrity_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
