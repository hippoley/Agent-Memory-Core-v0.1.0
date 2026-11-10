"""Standalone durable webhook delivery process.

Run separately from the API:
  MEMORY_DSN=... MEMORY_WEBHOOK_SIGNING_KEY=... \
  MEMORY_WEBHOOK_ALLOWED_HOSTS=hooks.example.com \
  python -m agent_memory_core.webhook_worker

The worker has no inbound listener. Outbound HTTPS targets are host-allowlisted.
"""
from __future__ import annotations

import argparse
import os
import signal
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--once",action="store_true",
                        help="enqueue and deliver one batch, then exit")
    parser.add_argument("--batch-size",type=int,default=100)
    parser.add_argument("--poll-seconds",type=float,default=2.0)
    args=parser.parse_args()
    if not 1<=args.batch_size<=1000 or args.poll_seconds<=0:
        parser.error("Invalid batch size or poll interval")
    dsn=os.getenv("MEMORY_DSN")
    master=os.getenv("MEMORY_WEBHOOK_SIGNING_KEY")
    hosts={x.strip().lower() for x in
           os.getenv("MEMORY_WEBHOOK_ALLOWED_HOSTS","").split(",") if x.strip()}
    if not dsn or not master or len(master)<32 or not hosts:
        parser.error("MEMORY_DSN, 32+ char MEMORY_WEBHOOK_SIGNING_KEY and MEMORY_WEBHOOK_ALLOWED_HOSTS required")
    from .postgres import PostgresMemory
    from .webhooks import WebhookWorker
    db=PostgresMemory(dsn)
    stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try:
        db.migrate()
        worker=WebhookWorker(db.conn,signing_master_key=master,
                             allowed_hosts=hosts)
        while not stopping:
            delivered=worker.consume(limit=args.batch_size)
            print({"claimed_or_attempted":delivered},flush=True)
            if args.once:
                break
            if delivered==0:
                time.sleep(args.poll_seconds)
    finally:
        db.close()


if __name__=="__main__":
    main()
