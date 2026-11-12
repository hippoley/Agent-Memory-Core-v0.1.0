"""Diagnose why a question recalls nothing: extraction gap or retrieval gap.

Ingests one LongMemEval question exactly like the harness does, then asks the
same store four different ways, so the failure is attributed to a layer instead
of guessed:

  * how many claims were written at all   -> extraction side
  * FTS with the raw question             -> the query as the product sends it
  * FTS with OR-joined terms              -> would OR semantics recover it
  * lexical term-overlap, ranked          -> would better ranking recover it

Usage
  python scripts/eval/diag_retrieval.py --report data/eval/longmemeval_cloud24_serial.json \
      --picks 2 --types single-session-preference,multi-session
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
import psycopg

ROOT = Path(__file__).resolve().parents[2]
DSN = "postgresql://memory:local-dev-only@127.0.0.1:55432/memory"


def load_harness():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "harness", ROOT / "scripts" / "eval" / "run_longmemeval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--base-url", default="http://127.0.0.1:8767")
    ap.add_argument("--key", required=True)
    ap.add_argument("--tenant", default="lme-eval")
    ap.add_argument("--picks", type=int, default=2)
    ap.add_argument("--types", default=None)
    args = ap.parse_args()

    harness = load_harness()
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    items = json.loads((ROOT / "data" / "eval" / "longmemeval_oracle.json")
                       .read_text(encoding="utf-8"))
    by_id = {item["question_id"]: item for item in items}

    types = {t.strip() for t in (args.types or "").split(",") if t.strip()}
    pool = [r for r in report["results"]
            if not r["correct"] and (not types or r["question_type"] in types)]
    pool.sort(key=lambda r: r["recalled"])
    picks = pool[:args.picks]

    api = harness.Api(args.base_url, args.key)
    conn = psycopg.connect(DSN, autocommit=True)

    def ask(sql, params):
        return conn.execute(sql, params).fetchall()

    for row in picks:
        item = by_id[row["question_id"]]
        print("=" * 78)
        print(f"{row['question_type']}  recalled={row['recalled']}")
        print(f"Q    : {row['question']}")
        print(f"GOLD : {row['gold']}")
        print(f"ANS  : {row['answer']}")

        api.forget_tenant(DSN, args.tenant)
        stats = harness.ingest(api, item)
        print(f"ingest: turns={stats['turns']} claims={stats['created']} "
              f"dups={stats['duplicates']} rejected={stats['rejected']}")

        total = ask("SELECT count(*) FROM memory_claims WHERE tenant_id=%s",
                    (args.tenant,))[0][0]
        indexed = ask("SELECT count(*) FROM memory_claim_search WHERE tenant_id=%s",
                      (args.tenant,))[0][0]
        print(f"stored: claims={total} search_rows={indexed}")

        question = row["question"]
        fts_and = ask(
            "SELECT count(*) FROM memory_claim_search s WHERE s.tenant_id=%s "
            "AND s.document @@ websearch_to_tsquery('simple',%s)",
            (args.tenant, question))[0][0]
        from agent_memory_core.hybrid import query_terms
        overlap = query_terms(question)
        fts_or = ask(
            "SELECT count(*) FROM memory_claim_search s WHERE s.tenant_id=%s "
            "AND s.document @@ to_tsquery('simple',%s)",
            (args.tenant, " | ".join(overlap)))[0][0] if overlap else -1
        print(f"fts : raw-question={fts_and}  or-terms={fts_or} "
              f"({len(overlap)} terms: {overlap[:8]})")

        hits = ask(
            "SELECT subject_id, predicate, left(value::text,60) "
            "FROM memory_claims WHERE tenant_id=%s LIMIT 12", (args.tenant,))
        print("sample claims:")
        for subject, predicate, value in hits:
            print(f"   {subject[:28]:28} | {predicate[:30]:30} | {value[:60]}")

        for label, query in (("raw", question), ("keywords", " ".join(overlap[:4]))):
            got = api.search(query, known_at=harness.parse_date(item["question_date"]),
                             limit=10)
            print(f"search[{label}] -> {len(got)} hits")
            for hit in got[:4]:
                print(f"   {hit['predicate'][:34]:34} = "
                      f"{json.dumps(hit.get('value'), ensure_ascii=False)[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
