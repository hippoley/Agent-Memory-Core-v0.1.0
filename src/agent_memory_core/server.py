"""Default single-tenant REST deployment wiring.

Vector search uses caller-supplied embeddings. AGE projection is opt-in with
MEMORY_ENABLE_AGE=1. This module does not start Temporal or an embedding model.
"""
import os

from .api import create_app


def search_factory():
    from .postgres import PostgresMemory
    from .hybrid import HybridMemorySearch

    db = PostgresMemory(os.environ["MEMORY_DSN"])
    graph = None
    if os.getenv("MEMORY_ENABLE_AGE", "0") == "1":
        from .age_graph import AgeProjection
        graph = AgeProjection(db.conn)
        graph.initialize()
    return HybridMemorySearch(db, graph=graph)


flags = {
    "semantic": os.getenv("MEMORY_ENABLE_VECTOR", "0") == "1",
    "graph": os.getenv("MEMORY_ENABLE_AGE", "0") == "1",
    # Object bytes and Temporal workflows are not exposed by the HTTP facade yet.
    "objects": False,
    "temporal": False,
}

app = create_app(search_factory=search_factory, capability_flags=flags)
