"""Supervised polling process for independent memory projection jobs."""
from __future__ import annotations

import argparse
import importlib
import logging
import os
import signal
import threading
import time


LOGGER = logging.getLogger("agent_memory_core.projection_worker")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one destination worker for Agent Memory Core projections."
    )
    parser.add_argument("--projection", required=True,
                        choices=("fts", "vector", "age"))
    parser.add_argument("--dsn", default=os.getenv("MEMORY_DSN"))
    parser.add_argument("--tenant-id", default=os.getenv("MEMORY_TENANT_ID"))
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--max-attempts", type=int, default=8)
    parser.add_argument("--base-retry-seconds", type=float, default=2.0)
    parser.add_argument("--max-retry-seconds", type=float, default=300.0)
    parser.add_argument("--embedder", default=os.getenv("MEMORY_EMBEDDER"),
                        help="For vector: dotted Python path module:callable.")
    parser.add_argument("--model-id", default=os.getenv("MEMORY_EMBEDDING_MODEL_ID"))
    parser.add_argument("--once", action="store_true",
                        help="Process one batch and exit instead of polling.")
    parser.add_argument("--log-level", default=os.getenv("MEMORY_WORKER_LOG_LEVEL", "INFO"))
    return parser


def _load_embedder(spec: str):
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("--embedder must use module.path:callable")
    module = importlib.import_module(module_name)
    embedder = getattr(module, attribute)
    if not callable(embedder):
        raise TypeError("Configured embedder is not callable")
    return embedder


def _handler_for(projection: str, conn, embedder_spec: str | None,
                 model_id: str | None):
    if projection == "fts":
        from .fts import project_claim_to_fts
        return project_claim_to_fts
    if projection == "age":
        from .age_graph import AgeProjection
        projector = AgeProjection(conn)
        projector.initialize()
        return projector.project_claim_event
    if not embedder_spec or not model_id:
        raise ValueError("Vector worker requires --embedder and --model-id")
    from .vector_projection import VectorProjection
    return VectorProjection(embedder=_load_embedder(embedder_spec), model_id=model_id)


def run(args) -> int:
    if not args.dsn:
        raise ValueError("Set --dsn or MEMORY_DSN")
    if not (1 <= args.batch_size <= 100):
        raise ValueError("--batch-size must be 1..100")
    if args.poll_interval <= 0:
        raise ValueError("--poll-interval must be positive")
    if args.max_attempts < 1:
        raise ValueError("--max-attempts must be at least 1")

    from .postgres import PostgresMemory
    from .projection_jobs import ProjectionWorker

    stopping = threading.Event()

    def stop(_signum, _frame):
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    db = None
    handler = None
    total = 0
    try:
        while not stopping.is_set():
            try:
                if db is None:
                    db = PostgresMemory(args.dsn)
                    handler = _handler_for(args.projection, db.conn,
                                           args.embedder, args.model_id)
                    LOGGER.info("Connected projection=%s tenant=%s",
                                args.projection, args.tenant_id or "*")
                worker = ProjectionWorker(
                    db.conn, max_attempts=args.max_attempts,
                    base_retry_seconds=args.base_retry_seconds,
                    max_retry_seconds=args.max_retry_seconds,
                )
                count = worker.consume(
                    projection_name=args.projection,
                    handler=handler,
                    limit=args.batch_size,
                    tenant_id=args.tenant_id,
                )
                total += count
                if count:
                    LOGGER.info("projection=%s processed=%d total=%d",
                                args.projection, count, total)
                if args.once:
                    return count
                if count == 0:
                    stopping.wait(args.poll_interval)
            except Exception:
                LOGGER.exception("Projection worker iteration failed; reconnecting")
                if db is not None:
                    try:
                        db.close()
                    except Exception:
                        LOGGER.exception("Failed to close database connection")
                db = None
                handler = None
                if not args.once:
                    stopping.wait(args.poll_interval)
                else:
                    raise
        return total
    finally:
        if db is not None:
            db.close()


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        return run(args)
    except Exception as exc:
        LOGGER.error("Projection worker stopped: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
