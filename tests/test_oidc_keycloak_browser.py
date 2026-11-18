"""Real Keycloak -> OIDC -> browser Session -> PostgreSQL memory lifecycle.

Opt in with MEMORY_KEYCLOAK_E2E=1. The workflow starts Keycloak, PostgreSQL and
the API, then this test creates a real IdP user and completes the browser flow.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.browser
if os.getenv("MEMORY_KEYCLOAK_E2E") != "1":
    pytest.skip("Opt-in real Keycloak OIDC acceptance", allow_module_level=True)

httpx = pytest.importorskip("httpx")
playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect


BASE_URL = os.getenv("MEMORY_E2E_BASE_URL", "http://127.0.0.1:8765")
KEYCLOAK = os.getenv("MEMORY_KEYCLOAK_BASE_URL", "http://keycloak.localhost:8081")
ADMIN_USER = os.getenv("KEYCLOAK_ADMIN", "admin")
ADMIN_PASSWORD = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "local-keycloak-admin-change-me")


def _create_idp_user() -> tuple[str, str]:
    with httpx.Client(timeout=15.0) as client:
        token_response = client.post(
            KEYCLOAK + "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": ADMIN_USER,
                "password": ADMIN_PASSWORD,
            },
        )
        token_response.raise_for_status()
        token = token_response.json()["access_token"]
        username = "amc-" + uuid.uuid4().hex[:12]
        password = "Local-E2E-" + uuid.uuid4().hex + "!"
        create = client.post(
            KEYCLOAK + "/admin/realms/agent-memory/users",
            headers={"Authorization": "Bearer " + token},
            json={
                "username": username,
                "email": username + "@example.test",
                "firstName": "Agent",
                "lastName": "Memory",
                "enabled": True,
                "emailVerified": True,
            },
        )
        create.raise_for_status()
        location = create.headers.get("Location", "")
        user_id = location.rsplit("/", 1)[-1]
        assert user_id, "Keycloak did not return the created user location"
        reset = client.put(
            KEYCLOAK + "/admin/realms/agent-memory/users/" + user_id + "/reset-password",
            headers={"Authorization": "Bearer " + token},
            json={"type": "password", "value": password, "temporary": False},
        )
        assert reset.status_code == 204, reset.text
        return username, password


def test_real_keycloak_login_creates_workspace_and_completes_memory_lifecycle():
    username, password = _create_idp_user()
    with playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(BASE_URL + "/v1/auth/oidc/login", wait_until="domcontentloaded")
            page.locator("#username").fill(username)
            page.locator("#password").fill(password)
            page.locator("#kc-login").click()
            page.wait_for_url(re.compile(re.escape(BASE_URL) + r"/dashboard/observatory\.html"),
                              timeout=30000)

            identity = page.evaluate("""async () => {
              const tenant = document.cookie.split('; ').find(x => x.startsWith('amc_tenant='));
              const tenantId = tenant ? decodeURIComponent(tenant.split('=').slice(1).join('=')) : '';
              const response = await fetch('/v1/identity/me', {
                credentials: 'same-origin',
                headers: {'X-Memory-Tenant': tenantId}
              });
              return {status: response.status, body: await response.json(), tenantId};
            }""")
            assert identity["status"] == 200, identity
            assert identity["body"]["role"] == "owner"
            assert identity["body"]["auth_type"] == "session"
            assert identity["tenantId"]
            original_tenant = identity["tenantId"]

            page.wait_for_function("document.querySelector('#workspaceSelect').options.length === 1")
            page.once("dialog", lambda dialog: dialog.accept("Second Workspace"))
            page.locator("#createWorkspaceBtn").click()
            page.wait_for_function("document.querySelector('#workspaceSelect').options.length === 2")
            second_tenant = page.evaluate("document.querySelector('#workspaceSelect').value")
            assert second_tenant and second_tenant != original_tenant
            page.locator("#workspaceSelect").select_option(original_tenant)
            page.wait_for_function(
                "id => document.cookie.split('; ').some(x => x.trim() === 'amc_tenant=' + id)",
                arg=original_tenant,
            )
            switched_identity = page.evaluate("""async (tenant) => {
              const response = await fetch('/v1/identity/me', {
                credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
              });
              return {status:response.status,body:await response.json()};
            }""", original_tenant)
            assert switched_identity["status"] == 200
            assert switched_identity["body"]["tenant_id"] == original_tenant

            now = datetime.now(timezone.utc).isoformat()
            created = page.evaluate("""async (now) => {
              const tenant = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_tenant=')).split('=').slice(1).join('='));
              const csrf = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_csrf=')).split('=').slice(1).join('='));
              const response = await fetch('/v1/memories', {
                method: 'POST', credentials: 'same-origin',
                headers: {'Content-Type':'application/json','X-Memory-Tenant':tenant,'X-CSRF-Token':csrf},
                body: JSON.stringify({
                  subject_id:'oidc-e2e-user', predicate:'onboarding_check',
                  value:{result:'keycloak-login-ok'}, source_id:'keycloak-e2e',
                  valid_from:now
                })
              });
              return {status:response.status,body:await response.json()};
            }""", now)
            assert created["status"] == 201, created
            memory_id = created["body"]["id"]

            listed = page.evaluate("""async () => {
              const tenant = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_tenant=')).split('=').slice(1).join('='));
              const response = await fetch('/v1/memories?limit=100&include_superseded=true', {
                credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
              });
              return {status:response.status,body:await response.json()};
            }""")
            assert listed["status"] == 200
            assert any(item.get("id") == memory_id for item in listed["body"]["items"])

            forgotten = page.evaluate("""async (memoryId) => {
              const tenant = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_tenant=')).split('=').slice(1).join('='));
              const csrf = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_csrf=')).split('=').slice(1).join('='));
              const response = await fetch('/v1/memories/' + memoryId, {
                method:'DELETE', credentials:'same-origin',
                headers:{'X-Memory-Tenant':tenant,'X-CSRF-Token':csrf}
              });
              return {status:response.status,body:await response.json()};
            }""", memory_id)
            assert forgotten["status"] == 200, forgotten
            active_after_forget = page.evaluate("""async () => {
              const tenant = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_tenant=')).split('=').slice(1).join('='));
              const response = await fetch('/v1/memories?limit=100', {
                credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
              });
              return {status:response.status,body:await response.json()};
            }""")
            assert active_after_forget["status"] == 200
            assert not any(item.get("id") == memory_id for item in active_after_forget["body"]["items"]), (
                "Forgotten claim remained in active memory listing"
            )

            page.reload(wait_until="domcontentloaded")
            after_reload = page.evaluate("""async () => {
              const tenant = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_tenant=')).split('=').slice(1).join('='));
              const response = await fetch('/v1/identity/me', {
                credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
              });
              return response.status;
            }""")
            assert after_reload == 200, "Browser session did not survive reload"

            logout = page.evaluate("""async () => {
              const tenant = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_tenant=')).split('=').slice(1).join('='));
              const csrf = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_csrf=')).split('=').slice(1).join('='));
              const response = await fetch('/v1/auth/logout', {
                method:'POST', credentials:'same-origin',
                headers:{'X-Memory-Tenant':tenant,'X-CSRF-Token':csrf}
              });
              return {status:response.status,body:await response.json()};
            }""")
            assert logout["status"] == 200 and logout["body"]["logged_out"] is True
            after_logout = page.evaluate("""async () => {
              const item = document.cookie.split('; ').find(x => x.startsWith('amc_tenant='));
              const tenant = item ? decodeURIComponent(item.split('=').slice(1).join('=')) : '';
              const response = await fetch('/v1/identity/me', {
                credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
              });
              return response.status;
            }""")
            assert after_logout == 401, "Revoked browser session remained usable"
            assert not errors, "Browser JavaScript errors: " + "; ".join(errors)
        finally:
            context.close()
            browser.close()
