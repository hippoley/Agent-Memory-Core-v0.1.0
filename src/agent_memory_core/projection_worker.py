"""Standalone projection worker process.

Run with MEMORY_DSN and optionally MEMORY_EMBEDDER=module:callable.
The embedder must be a local callable returning (model_id, vector).
"""
from __future__ import annotations

import importlib
import os
import signal
import time


def _load_embedder(spec: str | None):
    if not spec:
        return None
    module_name, separator, attribute = spec.partition(":")
    if not separator:
        raise ValueError("MEMORY_EMBEDDER must use module:callable")
    value = getattr(importlib.import_module(module_name), attribute)
    if not callable(value):
        raise TypeError("Configured embedder is not callable")
    return value


def run(*, poll_seconds: float = 1.0, batch_size: int = 20) -> None:
    if poll_seconds <= 0 or not 1 <= batch_size <= 100:
        raise ValueError("Invalid polling configuration")
    from .postgres import PostgresMemory
    from .projections import ProjectionWorker, PostgresProjectionHandlers

    dsn = os.environ["MEMORY_DSN"]
    embedder = _load_embedder(os.getenv("MEMORY_EMBEDDER"))
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        db = PostgresMemory(dsn)
        try:
            worker = ProjectionWorker(db.conn)
            handlers = PostgresProjectionHandlers(embedder=embedder)
            fts_done = worker.consume(channel="fts", handler=handlers.full_text, limit=batch_size)
            graph_done = worker.consume(channel="graph", handler=handlers.graph, limit=batch_size)
            vector_done = 0
            if embedder is not None:
                vector_done = worker.consume(channel="vector", handler=handlers.vector, limit=batch_size)
            if not (fts_done or graph_done or vector_done):
                time.sleep(poll_seconds)
        finally:
            db.close()


if __name__ == "__main__":
    run(
        poll_seconds=float(os.getenv("MEMORY_PROJECTION_POLL_SECONDS", "1")),
        batch_size=int(os.getenv("MEMORY_PROJECTION_BATCH_SIZE", "20")),
    )
