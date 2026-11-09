"""Fail-fast diagnostic for one-database co-installation."""
import os
from agent_memory_core.postgres import PostgresMemory

dsn=os.environ["MEMORY_TEST_DSN"]
db=PostgresMemory(dsn)
try:
    found=dict(db.conn.execute(
        "SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector','age')"
    ).fetchall())
    if set(found) != {"vector","age"}:
        raise SystemExit(f"Missing required extensions. Found: {found}")
    server=db.conn.execute("SHOW server_version").fetchone()[0]
    print("Verified PostgreSQL:",server,"extensions:",found)
finally:
    db.close()
