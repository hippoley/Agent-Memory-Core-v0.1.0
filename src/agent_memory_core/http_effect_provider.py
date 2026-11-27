"""HTTP adapter for a *real resource-server authorization boundary*.

The ACTUATE token is never sent to the reconciliation endpoint and the LOOKUP
token is never sent to the execution endpoint. This does not make a gateway
honest: real deployments must separately authorize both credentials.
"""
from __future__ import annotations

import json
from urllib import error, parse, request

from .durable_effects import ProviderRejected
from .provider_contract import ProviderCapabilities, ProviderContractError


class _RejectRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPDurableEffectProvider:
    """Wire contract: POST /effects, GET /effects/{stable-idempotency-key}.

    A lookup response MUST explicitly contain outcome: committed | not_found |
    unknown | failed. 404/redirects are not authoritative NOT_FOUND evidence.
    """

    def __init__(self, *, base_url: str, actuation_token: str, lookup_token: str,
                 idempotency_retention_seconds: int = 86400,
                 timeout_seconds: float = 5.0):
        parsed = parse.urlsplit(base_url)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme not in ("https", "http") or not parsed.netloc
                or parsed.username or parsed.password or parsed.query
                or parsed.fragment or parsed.path.rstrip("/").endswith("/effects")):
            raise ValueError("Provide an absolute HTTP(S) gateway base URL without credentials")
        if parsed.scheme == "http" and host not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Cleartext HTTP is permitted only for loopback tests")
        if (not actuation_token or not lookup_token or
                actuation_token == lookup_token or
                any(c in actuation_token + lookup_token for c in "\r\n")):
            raise ValueError("Distinct nonempty actuation and lookup credentials required")
        if (type(idempotency_retention_seconds) is not int
                or idempotency_retention_seconds < 1):
            raise ValueError("Positive idempotency retention required")
        if not 0 < timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be in (0,120]")
        self._url = base_url.rstrip("/")
        self._actuation = actuation_token
        self._lookup = lookup_token
        self._timeout = timeout_seconds
        self._opener = request.build_opener(_RejectRedirects())
        self.capabilities = ProviderCapabilities(
            idempotency=True, reconciliation=True, lookup_consistency="strong",
            idempotency_retention_seconds=idempotency_retention_seconds)

    def _json(self, *, method: str, path: str, token: str,
              payload: dict | None = None) -> dict:
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(self._url + path, data=body,
                              headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=self._timeout) as response:
                data = response.read(1048577)
        except error.HTTPError as exc:
            # Only an explicit *actuation* 401/403 is definitive rejection.
            # In particular, GET 404/403 is NEVER fabricated into NOT_FOUND.
            if method == "POST" and exc.code in (401, 403):
                raise ProviderRejected("Gateway denied actuation credential") from None
            raise ProviderContractError(
                "Gateway request failed with HTTP " + str(exc.code)) from None
        except error.URLError as exc:
            raise ConnectionError("Gateway connection failed; result may be UNKNOWN") from None
        if len(data) > 1048576:
            raise ProviderContractError("Gateway JSON response exceeds 1 MiB")
        try:
            result = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            raise ProviderContractError("Gateway returned invalid JSON") from None
        if not isinstance(result, dict):
            raise ProviderContractError("Gateway response must be a JSON object")
        return result

    def execute(self, effect_request: dict, *, idempotency_key: str) -> dict:
        if not isinstance(effect_request, dict) or not idempotency_key:
            raise ValueError("Effect request and stable idempotency key are required")
        response = self._json(method="POST", path="/effects",
                              token=self._actuation,
                              payload={"request": effect_request,
                                       "idempotency_key": idempotency_key})
        if not response.get("provider_reference"):
            raise ProviderContractError("Gateway actuation response lacks provider reference")
        return response

    def reconcile(self, effect_request: dict, *, idempotency_key: str,
                  provider_reference: str | None) -> dict:
        if not idempotency_key:
            raise ValueError("Stable idempotency key required for reconciliation")
        path = "/effects/" + parse.quote(idempotency_key, safe="")
        response = self._json(method="GET", path=path, token=self._lookup)
        if response.get("outcome") not in ("committed", "not_found", "unknown", "failed"):
            raise ProviderContractError("Gateway lookup returned invalid outcome")
        if response["outcome"] == "committed" and not response.get("provider_reference"):
            raise ProviderContractError("Committed lookup lacks provider reference")
        return response
