"""OIDC Authorization Code + PKCE client for the browser login flow.

Configuration is explicit and single-provider for the first deployment slice.
The transaction cookie is integrity-protected and short-lived; it contains no
access token or user data. ID tokens are verified against the provider JWKS.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True)
class OIDCConfig:
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    state_secret: bytes
    allow_signup: bool = False
    cookie_secure: bool = True

    @classmethod
    def from_env(cls) -> "OIDCConfig":
        issuer = os.getenv("MEMORY_OIDC_ISSUER", "").strip().rstrip("/")
        client_id = os.getenv("MEMORY_OIDC_CLIENT_ID", "").strip()
        client_secret = os.getenv("MEMORY_OIDC_CLIENT_SECRET", "")
        redirect_uri = os.getenv("MEMORY_OIDC_REDIRECT_URI", "").strip()
        raw_secret = os.getenv("MEMORY_OIDC_STATE_SECRET", "")
        if not all((issuer, client_id, client_secret, redirect_uri, raw_secret)):
            raise RuntimeError("OIDC provider configuration is incomplete")
        if len(raw_secret.encode("utf-8")) < 32:
            raise RuntimeError("MEMORY_OIDC_STATE_SECRET must contain at least 32 bytes")
        for value, name in ((issuer, "issuer"), (redirect_uri, "redirect URI")):
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.netloc:
                raise RuntimeError(f"OIDC {name} must use HTTPS")
        return cls(
            issuer=issuer, client_id=client_id, client_secret=client_secret,
            redirect_uri=redirect_uri, state_secret=raw_secret.encode("utf-8"),
            allow_signup=os.getenv("MEMORY_OIDC_ALLOW_SIGNUP", "0") == "1",
            cookie_secure=os.getenv("MEMORY_COOKIE_SECURE", "1") == "1",
        )


class OIDCClient:
    def __init__(self, config: OIDCConfig):
        self.config = config

    def _discovery(self) -> dict:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("Install the oidc extra: pip install -e '.[oidc]'") from exc
        url = self.config.issuer + "/.well-known/openid-configuration"
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            response = client.get(url)
            response.raise_for_status()
            metadata = response.json()
        if metadata.get("issuer", "").rstrip("/") != self.config.issuer:
            raise ValueError("OIDC discovery issuer mismatch")
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            endpoint = metadata.get(key)
            parsed = urlparse(endpoint or "")
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError(f"OIDC discovery returned an invalid {key}")
        return metadata

    def begin(self) -> tuple[str, str]:
        metadata = self._discovery()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _b64(hashlib.sha256(verifier.encode("ascii")).digest())
        transaction = {
            "state": state, "nonce": nonce, "verifier": verifier,
            "expires_at": int(time.time()) + 600,
        }
        raw = _b64(json.dumps(transaction, separators=(",", ":")).encode("utf-8"))
        signature = _b64(hmac.new(
            self.config.state_secret, raw.encode("ascii"), hashlib.sha256).digest())
        cookie = raw + "." + signature
        query = urlencode({
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        separator = "&" if "?" in metadata["authorization_endpoint"] else "?"
        return metadata["authorization_endpoint"] + separator + query, cookie

    def verify_transaction(self, cookie: str, returned_state: str) -> dict:
        try:
            raw, supplied = cookie.split(".", 1)
            expected = _b64(hmac.new(
                self.config.state_secret, raw.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(supplied, expected):
                raise ValueError("Invalid OIDC transaction signature")
            transaction = json.loads(_unb64(raw))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid OIDC transaction") from exc
        if not hmac.compare_digest(str(transaction.get("state", "")), str(returned_state or "")):
            raise ValueError("OIDC state mismatch")
        if int(transaction.get("expires_at", 0)) < int(time.time()):
            raise ValueError("OIDC transaction expired")
        if not transaction.get("nonce") or not transaction.get("verifier"):
            raise ValueError("OIDC transaction is incomplete")
        return transaction

    def exchange_and_verify(self, *, code: str, transaction: dict) -> dict:
        try:
            import httpx
            import jwt
            from jwt import PyJWKClient
        except ImportError as exc:
            raise RuntimeError("Install the oidc extra: pip install -e '.[oidc]'") from exc
        metadata = self._discovery()
        with httpx.Client(timeout=15.0, follow_redirects=False) as client:
            response = client.post(metadata["token_endpoint"], data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "code_verifier": transaction["verifier"],
            }, headers={"Accept": "application/json"})
            response.raise_for_status()
            token_response = response.json()
        id_token = token_response.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise ValueError("OIDC token response did not contain an ID token")
        signing_key = PyJWKClient(metadata["jwks_uri"], timeout=10).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token, signing_key.key, algorithms=["RS256", "ES256", "PS256"],
            audience=self.config.client_id, issuer=self.config.issuer,
            options={"require": ["exp", "iat", "iss", "sub", "aud", "nonce"]},
        )
        if not hmac.compare_digest(str(claims.get("nonce", "")), transaction["nonce"]):
            raise ValueError("OIDC nonce mismatch")
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise ValueError("OIDC subject is missing")
        return claims
