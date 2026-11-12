"""Container entrypoint: migrate the isolated local demo DB then serve HTTP.

Only for local development. Never use an administrator DSN as an Internet-facing
multi-tenant service identity.
"""
from __future__ import annotations
import os

def main() -> None:
    from .postgres import PostgresMemory
    dsn = os.environ["MEMORY_DSN"]
    store = PostgresMemory(dsn)
    try:
        store.migrate()
    finally:
        store.close()
    import uvicorn
    uvicorn.run("agent_memory_core.server:app", host="0.0.0.0", port=8765,
                proxy_headers=False, access_log=False)

if __name__ == "__main__":
    main()
