"""Default single-tenant REST deployment wiring.

Vector search uses caller-supplied embeddings. AGE projection is opt-in with
MEMORY_ENABLE_AGE=1. This module does not start Temporal or an embedding model.
"""
import os

from .api import create_app
from .objects import ObjectStore


def search_factory():
    from .postgres import PostgresMemory
    from .hybrid import HybridMemorySearch

    db = PostgresMemory(os.environ["MEMORY_DSN"])
    graph = None
    if os.getenv("MEMORY_ENABLE_AGE", "0") == "1":
        from .age_graph import AgeProjection
        try:
            graph = AgeProjection(db.conn)
            graph.initialize()
        except Exception:
            # A failed optional graph initialization must not prevent canonical
            # SQL and vector retrieval. Graph failure appears in diagnostics.
            class UnavailableGraph:
                def neighbors(self, **kwargs):
                    raise RuntimeError("Apache AGE graph adapter is unavailable")
            graph = UnavailableGraph()
    return HybridMemorySearch(db, graph=graph)


def object_store_factory():
    root = os.getenv("MEMORY_OBJECT_ROOT", "./data/objects")
    return ObjectStore(root)


flags = {
    "semantic": os.getenv("MEMORY_ENABLE_VECTOR", "0") == "1",
    "graph": os.getenv("MEMORY_ENABLE_AGE", "0") == "1",
    "objects": True,
    "temporal": False,
}

app = create_app(search_factory=search_factory,
                 object_store_factory=object_store_factory,
                 capability_flags=flags)
