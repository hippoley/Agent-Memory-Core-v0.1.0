"""Disposable real HTTP AMC server used only by the process-isolation acceptance test."""
import os

from agent_memory_core.api import create_app
from agent_memory_core.postgres import PostgresMemory

dsn = os.environ["MEMORY_TEST_DSN"]
with PostgresMemory(dsn) as _db:
    _db.migrate()

app = create_app(
    api_key=os.environ["MEMORY_API_KEY"],
    tenant_id=os.environ["MEMORY_TENANT_ID"],
    store_factory=lambda: PostgresMemory(dsn),
)
