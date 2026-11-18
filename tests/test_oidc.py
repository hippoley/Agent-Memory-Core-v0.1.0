"""Unit tests for OIDC browser transaction integrity."""
import json
import time

import pytest

from agent_memory_core.oidc import OIDCClient, OIDCConfig, _b64


def _client():
    return OIDCClient(OIDCConfig(
        issuer="https://issuer.example.test",
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://memory.example.test/v1/auth/oidc/callback",
        state_secret=b"x" * 32,
    ))


def _cookie(client, *, state="state-1", expires_at=None):
    transaction = {
        "state": state,
        "nonce": "nonce-1",
        "verifier": "verifier-1",
        "expires_at": expires_at if expires_at is not None else int(time.time()) + 300,
    }
    raw = _b64(json.dumps(transaction, separators=(",", ":")).encode())
    import hashlib
    import hmac
    signature = _b64(hmac.new(client.config.state_secret, raw.encode("ascii"),
                              hashlib.sha256).digest())
    return raw + "." + signature


def test_oidc_transaction_accepts_matching_signed_state():
    client = _client()
    transaction = client.verify_transaction(_cookie(client), "state-1")
    assert transaction["nonce"] == "nonce-1"
    assert transaction["verifier"] == "verifier-1"


@pytest.mark.parametrize("returned_state", ["wrong-state", "", None])
def test_oidc_transaction_rejects_state_mismatch(returned_state):
    client = _client()
    with pytest.raises(ValueError, match="state mismatch"):
        client.verify_transaction(_cookie(client), returned_state)


def test_oidc_transaction_rejects_tampered_cookie():
    client = _client()
    cookie = _cookie(client)
    with pytest.raises(ValueError, match="signature"):
        client.verify_transaction(cookie[:-1] + ("A" if cookie[-1] != "A" else "B"),
                                  "state-1")


def test_oidc_transaction_rejects_expired_cookie():
    client = _client()
    with pytest.raises(ValueError, match="expired"):
        client.verify_transaction(_cookie(client, expires_at=int(time.time()) - 1),
                                  "state-1")
