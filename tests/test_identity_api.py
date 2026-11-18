"""HTTP authorization tests for membership-backed sessions and API credentials."""
from fastapi.testclient import TestClient

from agent_memory_core.api import create_app


KEY = "legacy-key-for-backward-compatibility-123"
TENANT = "tenant-a"


class MemoryStore:
    def remember(self, **kwargs):
        assert kwargs["tenant_id"] == TENANT
        return "memory-1"

    def close(self):
        pass


class IdentityStoreFake:
    def __init__(self):
        self.role = "editor"
        self.revoked = False

    def list_session_tenants(self, *, token):
        if token != "valid-session" or self.revoked:
            return []
        return [{"tenant_id": TENANT, "display_name": "Tenant A",
                 "role": self.role, "membership_version": 1}]

    def authenticate_session(self, *, token, tenant_id):
        if token != "valid-session" or tenant_id != TENANT or self.revoked:
            return None
        return {"session_id": "session-1", "principal_id": "principal-1",
                "tenant_id": TENANT, "role": self.role,
                "membership_version": 1}

    def authenticate_api_credential(self, *, secret):
        if secret != "amc_valid_api_credential":
            return None
        return {"credential_id": "credential-1", "principal_id": "principal-1",
                "tenant_id": TENANT, "role": self.role,
                "membership_version": 1, "scopes": ["memory:read"]}

    def revoke_session(self, *, session_id, principal_id):
        if session_id == "session-1" and principal_id == "principal-1":
            self.revoked = True
            return True
        return False

    def close(self):
        pass


def _app(identity):
    return create_app(
        api_key=KEY, tenant_id="legacy-tenant",
        store_factory=MemoryStore,
        identity_store_factory=lambda: identity,
    )


def test_membership_session_reads_and_writes_then_immediate_role_downgrade():
    identity = IdentityStoreFake()
    client = TestClient(_app(identity))
    headers = {"Authorization": "Bearer valid-session", "X-Memory-Tenant": TENANT}

    assert client.get("/v1/capabilities", headers=headers).status_code == 200
    payload = {"subject_id": "room", "predicate": "noise",
               "value": {"level": "quiet"}, "source_id": "test",
               "valid_from": "2026-11-18T00:00:00Z"}
    assert client.post("/v1/memories", headers=headers, json=payload).status_code == 201

    identity.role = "reader"
    assert client.get("/v1/capabilities", headers=headers).status_code == 200
    denied = client.post("/v1/memories", headers=headers, json=payload)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Reader membership is read-only"


def test_api_credential_is_tenant_bound_and_scope_limited():
    identity = IdentityStoreFake()
    client = TestClient(_app(identity))
    headers = {"Authorization": "Bearer amc_valid_api_credential",
               "X-Memory-Tenant": TENANT}
    assert client.get("/v1/capabilities", headers=headers).status_code == 200
    payload = {"subject_id": "room", "predicate": "noise",
               "value": {"level": "quiet"}, "source_id": "test",
               "valid_from": "2026-11-18T00:00:00Z"}
    denied = client.post("/v1/memories", headers=headers, json=payload)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Credential scope is insufficient"

    wrong_tenant = {**headers, "X-Memory-Tenant": "tenant-b"}
    assert client.get("/v1/capabilities", headers=wrong_tenant).status_code == 401


def test_cookie_session_requires_csrf_for_mutations():
    identity = IdentityStoreFake()
    client = TestClient(_app(identity))
    client.cookies.set("amc_session", "valid-session")
    client.cookies.set("amc_csrf", "csrf-secret")
    headers = {"X-Memory-Tenant": TENANT}
    payload = {"subject_id": "room", "predicate": "noise",
               "value": {"level": "quiet"}, "source_id": "test",
               "valid_from": "2026-11-18T00:00:00Z"}
    assert client.get("/v1/capabilities", headers=headers).status_code == 200
    assert client.post("/v1/memories", headers=headers, json=payload).status_code == 403
    assert client.post("/v1/memories", headers={**headers, "X-CSRF-Token": "csrf-secret"},
                       json=payload).status_code == 201


def test_legacy_single_tenant_bearer_key_remains_compatible():
    identity = IdentityStoreFake()
    client = TestClient(_app(identity))
    response = client.get("/v1/capabilities",
        headers={"Authorization": "Bearer " + KEY})
    assert response.status_code == 200
    assert response.json()["tenant_id"] == "legacy-tenant"


def test_identity_me_and_logout_revoke_the_current_session():
    identity = IdentityStoreFake()
    client = TestClient(_app(identity))
    headers = {"Authorization": "Bearer valid-session", "X-Memory-Tenant": TENANT}
    me = client.get("/v1/identity/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["principal_id"] == "principal-1"
    assert me.json()["role"] == "editor"
    logout = client.post("/v1/auth/logout", headers=headers)
    assert logout.status_code == 200
    assert logout.json()["logged_out"] is True
    assert client.get("/v1/identity/me", headers=headers).status_code == 401


def test_identity_tenant_discovery_does_not_require_preselected_tenant():
    identity = IdentityStoreFake()
    client = TestClient(_app(identity))
    response = client.get("/v1/identity/tenants",
        headers={"Authorization": "Bearer valid-session"})
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["items"][0]["tenant_id"] == TENANT


def test_oidc_login_callback_sets_session_csrf_and_workspace_cookies(monkeypatch):
    from agent_memory_core import oidc
    from fastapi.testclient import TestClient

    config = oidc.OIDCConfig(
        issuer="https://issuer.example.test",
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://memory.example.test/v1/auth/oidc/callback",
        state_secret=b"x" * 32,
        allow_signup=True,
        cookie_secure=False,
    )
    monkeypatch.setattr(oidc.OIDCConfig, "from_env", classmethod(lambda cls: config))
    monkeypatch.setattr(oidc.OIDCClient, "begin",
                        lambda self: ("https://issuer.example.test/authorize", "signed-tx"))
    monkeypatch.setattr(oidc.OIDCClient, "verify_transaction",
                        lambda self, cookie, state: {"nonce": "nonce", "verifier": "verifier"})
    monkeypatch.setattr(oidc.OIDCClient, "exchange_and_verify",
                        lambda self, code, transaction: {
                            "sub": "oidc-user-1", "email": "user@example.test",
                            "email_verified": True, "name": "OIDC User"})

    class OnboardingIdentity(IdentityStoreFake):
        def __init__(self):
            super().__init__()
            self.closed = False
            self.login_args = None

        def complete_oidc_login(self, **kwargs):
            self.login_args = kwargs
            return {
                "token": "new-session-token",
                "tenants": [{"tenant_id": "workspace-1", "role": "owner"}],
            }

        def close(self):
            self.closed = True

    identity = OnboardingIdentity()
    client = TestClient(create_app(
        api_key=KEY, tenant_id="legacy-tenant", store_factory=MemoryStore,
        identity_store_factory=lambda: identity))

    start = client.get("/v1/auth/oidc/login", follow_redirects=False)
    assert start.status_code == 302
    assert start.headers["location"] == "https://issuer.example.test/authorize"
    assert "amc_oidc_tx" in start.cookies

    callback = client.get(
        "/v1/auth/oidc/callback?code=authorization-code&state=state-1",
        follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers["location"] == "/dashboard/observatory.html"
    assert callback.cookies["amc_session"] == "new-session-token"
    assert callback.cookies["amc_tenant"] == "workspace-1"
    assert "amc_csrf" in callback.cookies
    assert identity.login_args["issuer"] == config.issuer
    assert identity.login_args["subject"] == "oidc-user-1"
    assert identity.login_args["allow_signup"] is True
    assert identity.closed is True
