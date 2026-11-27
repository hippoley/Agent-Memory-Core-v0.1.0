"""Controlled true TCP issuer process, not an in-memory signer fixture."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
from urllib.error import HTTPError

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_memory_core.source_attestation import (
    SourceTrustPolicy, review_signed_handoff_claim,
)
from test_source_attestation import accepted_packet

ROOT = Path(__file__).resolve().parents[1]
ISSUER = ROOT / "benchmarks" / "handoff" / "independent_contract_issuer.py"


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_signed_source_is_served_by_separate_tcp_process_with_private_key_isolation():
    port = _free_port()
    private = Ed25519PrivateKey.generate()
    key = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    # Only source issuer subprocess receives the private key. The Agent
    # and the verifier have the pinned public key but cannot sign sources.
    env = dict(os.environ, AMC_TEST_ISSUER_PRIVATE_KEY=base64.urlsafe_b64encode(key).decode(),
               AMC_TEST_ISSUER_TENANT="tenant-a",
               AMC_TEST_ISSUER_SUBJECT="supplier-001")
    service = subprocess.Popen(
        [sys.executable, str(ISSUER), "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        document = None
        for _ in range(100):
            if service.poll() is not None:
                raise AssertionError("independent issuer exited early")
            try:
                with urlopen(f"http://127.0.0.1:{port}/current", timeout=.5) as resp:
                    document = json.load(resp)
                break
            except OSError:
                time.sleep(.05)
        assert document is not None, "source issuer did not become ready"
        trust = SourceTrustPolicy(
            issuer_public_keys={"independent-contract-source": public},
            active_revisions={("tenant-a", "supplier-001", "payment_period"): "supplier-contract:v3"},
        )
        pack = accepted_packet()
        pack["payload"]["claims"][0]["source_id"] = "supplier-contract:v3"
        # Existing AMC handoff integrity must be re-sealed after changing
        # the source revision. Real integrations fetch this via AMC HTTP.
        import hashlib
        payload = pack["payload"]
        payload.pop("sha256")
        payload["sha256"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        params = dict(
            accepted_handoff=pack, subject_id="supplier-001",
            predicate="payment_period", proposed_value={"period": "quarterly"},
            policy=trust, tenant_id="tenant-a", requested_use="plan",
        )
        verified = review_signed_handoff_claim(
            signed_source_envelope=document, **params)
        assert verified.state == "VERIFIED"
        assert verified.allowed_uses == ("plan",)
        # The attacker cannot replace the source using their own signing
        # authority even when all content fields and timestamps match.
        attacker = Ed25519PrivateKey.generate()
        fake = json.loads(json.dumps(document))
        fake["signature"] = base64.urlsafe_b64encode(
            attacker.sign(json.dumps(fake["body"], sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False,
                         allow_nan=False).encode())).rstrip(b"=").decode()
        blocked = review_signed_handoff_claim(
            signed_source_envelope=fake, **params)
        assert blocked.reason == "SOURCE_SIGNATURE_INVALID"
        assert not blocked.permitted
        # The valid remote receipt still does not authorize a Provider action.
        refused = review_signed_handoff_claim(
            signed_source_envelope=document, **{**params, "requested_use": "approve"})
        assert not refused.permitted
        assert refused.reason == "EXECUTION_REQUIRES_SEPARATE_AUTHORITY"
    finally:
        service.terminate()
        try:
            service.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            service.kill()
            service.communicate(timeout=5)
