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
        # The optional local identity profile uses the same isolated database
        # as both migration administrator and control-plane service. This is
        # deliberately not the production role layout; production migrations
        # are applied by the operator and the API uses memory_identity_service.
        if os.getenv("MEMORY_LOCAL_IDENTITY_MIGRATE", "0") == "1":
            from importlib.resources import files
            migration = files("agent_memory_core").joinpath(
                "resources/005_identity_foundation.sql")
            store.conn.execute(migration.read_text(encoding="utf-8"))
    finally:
        store.close()
    import uvicorn
    uvicorn.run("agent_memory_core.server:app", host="0.0.0.0", port=8765,
                proxy_headers=False, access_log=False)

if __name__ == "__main__":
    main()
