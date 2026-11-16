"""Read-only PostgreSQL FTS query diagnostic; requires an explicit database."""
from __future__ import annotations
import argparse
import os
import psycopg
from agent_memory_core.hybrid import query_terms


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default=os.getenv("MEMORY_TEST_DSN"))
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--query", required=True)
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("Supply --dsn or MEMORY_TEST_DSN; never guess a target database")
    with psycopg.connect(args.dsn, autocommit=True) as conn:
        tenant, query = args.tenant, args.query
        total = conn.execute("SELECT count(*) FROM memory_claims WHERE tenant_id=%s",
                             (tenant,)).fetchone()[0]
        indexed = conn.execute("SELECT count(*) FROM memory_claim_search WHERE tenant_id=%s",
                               (tenant,)).fetchone()[0]
        print("claims", total, "indexed", indexed)
        and_count = conn.execute(
            "SELECT count(*) FROM memory_claim_search s WHERE s.tenant_id=%s "
            "AND s.document @@ websearch_to_tsquery('simple',%s)",
            (tenant, query)).fetchone()[0]
        terms = query_terms(query)
        print("fts AND", and_count, "terms", terms)
        if not terms:
            print("fts OR", 0)
            return 0
        expression = " | ".join(terms)
        or_count = conn.execute(
            "SELECT count(*) FROM memory_claim_search s WHERE s.tenant_id=%s "
            "AND s.document @@ to_tsquery('simple',%s)",
            (tenant, expression)).fetchone()[0]
        print("fts OR", or_count)
        rows = conn.execute(
            "SELECT c.predicate, left(c.value::text,70), "
            "ts_rank_cd(s.document, to_tsquery('simple',%s)) r "
            "FROM memory_claim_search s JOIN memory_claims c ON c.id=s.memory_id "
            "WHERE s.tenant_id=%s AND s.document @@ to_tsquery('simple',%s) "
            "ORDER BY r DESC LIMIT 6",
            (expression, tenant, expression)).fetchall()
        for pred, value, score in rows:
            print(f"{score:.4f} {pred[:32]} = {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
