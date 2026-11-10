"""Explicit PostgreSQL FTS projection polling worker.

Run as a dedicated process. Each SQL write and delivery receipt shares one
transaction. This is not a hosted LLM, vector embedder or AGE worker.
"""
from __future__ import annotations
import argparse
import os
import time

from .postgres import PostgresMemory
from .outbox import ProjectionOutboxWorker, project_claim_fts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consumer-id", default="fts-v1")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.batch_size <= 100 or not 0.1 <= args.poll_seconds <= 60:
        parser.error("batch-size must be 1..100; poll-seconds must be 0.1..60")
    dsn = os.environ.get("MEMORY_DSN")
    if not dsn:
        parser.error("MEMORY_DSN is required")
    # Operator should run schema migrations before starting this worker.
    while True:
        db = PostgresMemory(dsn)
        try:
            done = ProjectionOutboxWorker(db.conn).consume(
                consumer_id=args.consumer_id, event_type="memory.claim.created",
                handler=project_claim_fts, limit=args.batch_size,
            )
        finally:
            db.close()
        if args.once:
            return 0
        if done == 0:
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
