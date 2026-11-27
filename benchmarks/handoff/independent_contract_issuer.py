"""Independent, controlled contract-issuer process for source admission tests.

This process holds the signing key, the Agent process never does. Not a
production issuer; there is no TLS or authenticated control plane.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def run(port):
    signing_key = Ed25519PrivateKey.from_private_bytes(
        base64.urlsafe_b64decode(os.environ["AMC_TEST_ISSUER_PRIVATE_KEY"]))
    tenant = os.environ["AMC_TEST_ISSUER_TENANT"]
    subject = os.environ["AMC_TEST_ISSUER_SUBJECT"]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/current":
                self.send_error(404)
                return
            now = datetime.now(timezone.utc)
            body = {
                "schema": "amc.source-attestation.v1",
                "issuer": os.environ.get("AMC_TEST_ISSUER_ID", "independent-contract-source"),
                "tenant_id": tenant,
                "subject_id": subject,
                "predicate": "payment_period",
                "source_id": "supplier-contract:v3",
                "value": {os.environ.get("AMC_TEST_ISSUER_VALUE_FIELD", "period"): "quarterly"},
                "issued_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=5)).isoformat(),
            }
            signed = {
                "body": body,
                "signature": base64.urlsafe_b64encode(
                    signing_key.sign(canonical(body))).rstrip(b"=").decode("ascii"),
            }
            payload = canonical(signed)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *_):
            pass
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    run(parser.parse_args().port)
