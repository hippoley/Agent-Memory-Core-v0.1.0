"""Explicit PostgreSQL vector, FTS and AGE projection polling worker.

Run as a dedicated process. Each SQL write and delivery receipt shares one
transaction. Embeddings load from a pre-existing local model directory only.
"""
from __future__ import annotations
import argparse
import os
import time

from .postgres import PostgresMemory
from .outbox import ProjectionOutboxWorker, project_claim_fts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projection", choices=("fts", "age", "vector"), default="fts")
    parser.add_argument("--consumer-id", default=None)
    parser.add_argument("--model-path", default=None,
                        help="Local directory with an offline SentenceTransformer")
    parser.add_argument("--model-id", default=None,
                        help="Stable model/version identifier for pgvector profiles")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--backfill-age-tenant", default=None,
                        help="Operator-only one-shot AGE repair for this tenant")
    args = parser.parse_args(argv)
    if not 1 <= args.batch_size <= 100 or not 0.1 <= args.poll_seconds <= 60:
        parser.error("batch-size must be 1..100; poll-seconds must be 0.1..60")
    dsn = os.environ.get("MEMORY_DSN")
    if not dsn:
        parser.error("MEMORY_DSN is required")
    if args.backfill_age_tenant is not None:
        from .age_graph import backfill_age_tenant
        import json
        db = PostgresMemory(dsn)
        try:
            result = backfill_age_tenant(db.conn, tenant_id=args.backfill_age_tenant,
                                         batch_size=args.batch_size)
        finally:
            db.close()
        print(json.dumps(result, sort_keys=True))
        return 0 if result["complete"] else 1
    embedding_handler = None
    if args.projection == "vector":
        if not args.model_path or not args.model_id:
            parser.error("--projection vector requires --model-path and --model-id")
        from .embedding_projection import local_sentence_transformer, make_embedding_handler
        embedding_handler = make_embedding_handler(
            model_id=args.model_id, embed=local_sentence_transformer(args.model_path))
    # Operator should run schema migrations before starting this worker.
    while True:
        db = PostgresMemory(dsn)
        try:
            worker = ProjectionOutboxWorker(db.conn)
            if args.projection == "fts":
                done = worker.consume(
                    consumer_id=args.consumer_id or "fts-v1",
                    event_type="memory.claim.created",
                    handler=project_claim_fts, limit=args.batch_size,
                )
            elif args.projection == "vector":
                done = worker.consume(
                    consumer_id=args.consumer_id or "vector:" + args.model_id,
                    event_type="memory.claim.created",
                    handler=embedding_handler, limit=args.batch_size,
                )
            else:
                from .age_graph import project_age_from_outbox
                consumer = args.consumer_id or "age-v1"
                done = 0
                for event_type in ("memory.edge.created", "memory.edge.retired",
                                   "memory.claim.created"):
                    done += worker.consume(
                        consumer_id=consumer, event_type=event_type,
                        handler=project_age_from_outbox, limit=args.batch_size,
                    )
        finally:
            db.close()
        if args.once:
            return 0
        if done == 0:
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
