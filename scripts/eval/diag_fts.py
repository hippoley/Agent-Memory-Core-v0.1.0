"""Compare FTS query semantics against one tenant's claims."""
import sys
import psycopg
from agent_memory_core.hybrid import query_terms

DSN = "postgresql://memory:local-dev-only@127.0.0.1:55432/memory"
tenant = sys.argv[1] if len(sys.argv) > 1 else "lme-eval"
query = sys.argv[2] if len(sys.argv) > 2 else (
    "How many days did I spend on camping trips in the United States this year?")

conn = psycopg.connect(DSN, autocommit=True)
print("claims", conn.execute("select count(*) from memory_claims where tenant_id=%s",
                             (tenant,)).fetchone()[0])
print("indexed", conn.execute("select count(*) from memory_claim_search where tenant_id=%s",
                              (tenant,)).fetchone()[0])

and_n = conn.execute(
    "select count(*) from memory_claim_search s where s.tenant_id=%s "
    "and s.document @@ websearch_to_tsquery('simple',%s)", (tenant, query)).fetchone()[0]
terms = query_terms(query)
or_n = conn.execute(
    "select count(*) from memory_claim_search s where s.tenant_id=%s "
    "and s.document @@ to_tsquery('simple',%s)",
    (tenant, " | ".join(terms))).fetchone()[0]
print(f"terms({len(terms)}): {terms}")
print(f"fts AND (websearch_to_tsquery): {and_n}")
print(f"fts OR  (to_tsquery |):         {or_n}")

rows = conn.execute(
    "select c.predicate, left(c.value::text,70), "
    "ts_rank_cd(s.document, to_tsquery('simple',%s)) r "
    "from memory_claim_search s join memory_claims c on c.id=s.memory_id "
    "where s.tenant_id=%s and s.document @@ to_tsquery('simple',%s) "
    "order by r desc limit 6", (" | ".join(terms), tenant, " | ".join(terms))).fetchall()
for predicate, value, rank in rows:
    print(f"   {rank:.4f} {predicate[:32]:32} = {value}")
