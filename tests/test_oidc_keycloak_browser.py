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

            # Full product path: Observatory UI -> authenticated API -> PostgreSQL.
            # Do not call the mutation endpoints directly for this lifecycle.
            page.wait_for_function("!document.querySelector('#writePanel').hidden")
            page.locator("#writeSubject").fill("observatory-ui-user")
            page.locator("#writePredicate").fill("preferred_language")
            page.locator("#writeValue").fill('{"language":"English"}')
            page.locator("#writeSource").fill("observatory-ui:keycloak-e2e:create")
            page.locator("#saveMemory").click()
            expect(page.locator("#mutationNote")).to_contain_text("Memory created and independently read back.", timeout=15000)
            page.wait_for_function("""() => {
              const picker = document.querySelector('#claimPicker');
              return Array.from(picker.options).some(option =>
                option.textContent.includes('observatory-ui-user / preferred_language'));
            }""")
            ui_created = page.evaluate("""() => {
              const option = Array.from(document.querySelector('#claimPicker').options)
                .find(item => item.textContent.includes('observatory-ui-user / preferred_language'));
              return option.value;
            }""")
            assert ui_created

            # The picker is the user's actual selection control; it must update
            # the revision target rather than merely changing button decoration.
            page.locator("#claimPicker").select_option(ui_created)
            page.locator("#targetSelected").click()
            expect(page.locator("#saveMemory")).to_contain_text("Replace selected memory")
            page.locator("#writeValue").fill('{"language":"Chinese"}')
            page.locator("#writeSource").fill("observatory-ui:keycloak-e2e:revision")
            page.locator("#saveMemory").click()
            expect(page.locator("#mutationNote")).to_contain_text("Revision committed and independently read back.", timeout=15000)
            ui_revision = page.evaluate("""async () => {
              const tenant = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_tenant=')).split('=').slice(1).join('='));
              const [activeResponse, historyResponse] = await Promise.all([
                fetch('/v1/memories?subject_id=observatory-ui-user&predicate=preferred_language', {
                  credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
                }),
                fetch('/v1/memories?subject_id=observatory-ui-user&predicate=preferred_language&include_superseded=true', {
                  credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
                })
              ]);
              return {active:await activeResponse.json(),history:await historyResponse.json()};
            }""")
            assert len(ui_revision["active"]["items"]) == 1, ui_revision
            assert ui_revision["active"]["items"][0]["value"]["language"] == "Chinese"
            assert len(ui_revision["history"]["items"]) == 2, ui_revision
            assert {item["value"]["language"] for item in ui_revision["history"]["items"]} == {"English","Chinese"}

            page.once("dialog", lambda dialog: dialog.accept())
            page.locator("#forgetSelected").click()
            expect(page.locator("#mutationNote")).to_contain_text("Soft-forget committed and independently read back for", timeout=15000)
            ui_forgotten = page.evaluate("""async () => {
              const tenant = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_tenant=')).split('=').slice(1).join('='));
              const [activeResponse, historyResponse] = await Promise.all([
                fetch('/v1/memories?subject_id=observatory-ui-user&predicate=preferred_language', {
                  credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
                }),
                fetch('/v1/memories?subject_id=observatory-ui-user&predicate=preferred_language&include_superseded=true', {
                  credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
                })
              ]);
              return {active:await activeResponse.json(),history:await historyResponse.json()};
            }""")
            assert ui_forgotten["active"]["items"] == [], ui_forgotten
            assert len(ui_forgotten["history"]["items"]) == 2, ui_forgotten

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

            # Correct a canonical claim through the real browser session and
            # prove both the active value and superseded audit version survive.
            revision_one_at = datetime.now(timezone.utc).isoformat()
            revision_one = page.evaluate("""async (now) => {
              const tenant = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_tenant=')).split('=').slice(1).join('='));
              const csrf = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_csrf=')).split('=').slice(1).join('='));
              const response = await fetch('/v1/memories', {
                method:'POST', credentials:'same-origin',
                headers:{'Content-Type':'application/json','X-Memory-Tenant':tenant,'X-CSRF-Token':csrf},
                body:JSON.stringify({
                  subject_id:'oidc-e2e-user', predicate:'preferred_language',
                  value:{language:'English'}, source_id:'keycloak-e2e-revision-1',
                  valid_from:now
                })
              });
              return {status:response.status,body:await response.json()};
            }""", revision_one_at)
            assert revision_one["status"] == 201, revision_one
            revision_two_at = datetime.now(timezone.utc).isoformat()
            assert revision_two_at > revision_one_at
            revision_two = page.evaluate("""async ({now, replacesId}) => {
              const tenant = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_tenant=')).split('=').slice(1).join('='));
              const csrf = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_csrf=')).split('=').slice(1).join('='));
              const response = await fetch('/v1/memories', {
                method:'POST', credentials:'same-origin',
                headers:{'Content-Type':'application/json','X-Memory-Tenant':tenant,'X-CSRF-Token':csrf},
                body:JSON.stringify({
                  subject_id:'oidc-e2e-user', predicate:'preferred_language',
                  value:{language:'Chinese'}, source_id:'keycloak-e2e-revision-2',
                  valid_from:now, replaces_id:replacesId
                })
              });
              return {status:response.status,body:await response.json()};
            }""", {"now":revision_two_at,"replacesId":revision_one["body"]["id"]})
            assert revision_two["status"] == 201, revision_two
            revision_state = page.evaluate("""async () => {
              const tenant = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_tenant=')).split('=').slice(1).join('='));
              const [activeResponse, historyResponse] = await Promise.all([
                fetch('/v1/memories?subject_id=oidc-e2e-user&predicate=preferred_language', {
                  credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
                }),
                fetch('/v1/memories?subject_id=oidc-e2e-user&predicate=preferred_language&include_superseded=true', {
                  credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
                })
              ]);
              return {active:await activeResponse.json(),history:await historyResponse.json()};
            }""")
            assert len(revision_state["active"]["items"]) == 1, revision_state
            assert revision_state["active"]["items"][0]["value"]["language"] == "Chinese"
            assert len(revision_state["history"]["items"]) == 2, revision_state
            assert {item["value"]["language"] for item in revision_state["history"]["items"]} == {"English","Chinese"}

            # Product E2E mode extends the same browser lifecycle into the real
            # Temporal worker. The regular Keycloak job intentionally stays
            # focused on identity + memory and does not require Temporal.
            if os.getenv("MEMORY_TEMPORAL_E2E") == "1":
                page.reload(wait_until="domcontentloaded")
                page.wait_for_function("!document.querySelector('#writePanel').hidden")
                page.wait_for_function("""id => Array.from(document.querySelector('#claimPicker').options)
                    .some(option => option.value === id)""", arg=revision_two["body"]["id"])
                page.locator("#claimPicker").select_option(revision_two["body"]["id"])
                page.locator("#verifySelected").click()
                expect(page.locator("#workflowNote")).to_contain_text(
                    "Temporal verification: COMPLETED.", timeout=30000
                )

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

            # Product E2E exports a short-lived, tenant-scoped credential
            # for a separate Python project installed from the built wheel.
            # The consumer runs in a fresh venv in the next CI step and has no
            # browser cookies, server imports, or database connection.
            if os.getenv("MEMORY_EXTERNAL_SDK_E2E") == "1":
                external_credential = page.evaluate("""async () => {
                  const tenant = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_tenant=')).split('=').slice(1).join('='));
                  const csrf = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_csrf=')).split('=').slice(1).join('='));
                  const response = await fetch('/v1/identity/api-credentials', {
                    method:'POST', credentials:'same-origin',
                    headers:{'Content-Type':'application/json','X-Memory-Tenant':tenant,'X-CSRF-Token':csrf},
                    body:JSON.stringify({name:'Independent third-party E2E',
                      scopes:['memory:read','memory:write'],expires_in_days:1})
                  });
                  return {status:response.status,body:await response.json(),tenant};
                }""")
                assert external_credential["status"] == 201, external_credential
                secret = external_credential["body"]["secret"]
                print("::add-mask::" + secret)
                github_env = os.getenv("GITHUB_ENV")
                if github_env:
                    with open(github_env, "a", encoding="utf-8") as env_file:
                        env_file.write("MEMORY_API_KEY=" + secret + "\n")
                        env_file.write("MEMORY_TENANT_ID=" + external_credential["tenant"] + "\n")
                        env_file.write("MEMORY_BASE_URL=" + BASE_URL + "\n")
                        env_file.write("MEMORY_RUN_TEMPORAL=1\n")

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

            # A second, independently provisioned Keycloak identity must not
            # inherit the first user's workspace or be able to select it.
            # Register the second user through Keycloak's public self-service
            # registration page; do not provision this identity through Admin API.
            username2 = "amc-self-" + uuid.uuid4().hex[:12]
            password2 = "Local-Self-Register-" + uuid.uuid4().hex + "!"
            context2 = browser.new_context(viewport={"width": 1440, "height": 900})
            page2 = context2.new_page()
            errors2: list[str] = []
            page2.on("pageerror", lambda error: errors2.append(str(error)))
            try:
                page2.goto(BASE_URL + "/v1/auth/oidc/login", wait_until="domcontentloaded")
                page2.locator("#kc-registration a").click()
                page2.wait_for_url(re.compile(r".*/realms/agent-memory/login-actions/registration.*"),
                                   timeout=15000)
                page2.locator('input[name="firstName"]').fill("Self")
                page2.locator('input[name="lastName"]').fill("Registered")
                page2.locator('input[name="email"]').fill(username2 + "@example.test")
                page2.locator('input[name="username"]').fill(username2)
                page2.locator('input[name="password"]').fill(password2)
                page2.locator('input[name="password-confirm"]').fill(password2)
                # Keycloak 26.2.5 default registration theme has no kc-register id.
                register_submit = page2.locator('#kc-register-form input[type="submit"], #kc-register-form button[type="submit"]')
                expect(register_submit).to_have_count(1)
                register_submit.click()
                page2.wait_for_url(
                    re.compile(re.escape(BASE_URL) + r"/dashboard/observatory\.html"),
                    timeout=30000,
                )
                second_identity = page2.evaluate("""async () => {
                  const item = document.cookie.split('; ').find(x => x.startsWith('amc_tenant='));
                  const tenant = item ? decodeURIComponent(item.split('=').slice(1).join('=')) : '';
                  const response = await fetch('/v1/identity/me', {
                    credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
                  });
                  return {status:response.status,body:await response.json(),tenant};
                }""")
                assert second_identity["status"] == 200, second_identity
                assert second_identity["body"]["role"] == "owner"
                assert second_identity["body"]["principal_id"] != identity["body"]["principal_id"]
                assert second_identity["tenant"] != original_tenant

                # Self-registration must lead to a usable product, not only a session.
                page2.wait_for_function("!document.querySelector('#writePanel').hidden")
                page2.locator("#writeSubject").fill("self-registered-user")
                page2.locator("#writePredicate").fill("onboarding_status")
                page2.locator("#writeValue").fill('{"status":"registered"}')
                page2.locator("#writeSource").fill("observatory-ui:self-registration:create")
                page2.locator("#saveMemory").click()
                expect(page2.locator("#mutationNote")).to_contain_text("Memory created and independently read back.", timeout=15000)
                page2.locator("#targetSelected").click()
                expect(page2.locator("#saveMemory")).to_contain_text("Replace selected memory")
                page2.locator("#writeValue").fill('{"status":"verified"}')
                page2.locator("#writeSource").fill("observatory-ui:self-registration:revision")
                page2.locator("#saveMemory").click()
                expect(page2.locator("#mutationNote")).to_contain_text("Revision committed and independently read back.", timeout=15000)
                self_service_state = page2.evaluate("""async () => {
                  const tenant = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_tenant=')).split('=').slice(1).join('='));
                  const [activeResponse, historyResponse] = await Promise.all([
                    fetch('/v1/memories?subject_id=self-registered-user&predicate=onboarding_status', {
                      credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
                    }),
                    fetch('/v1/memories?subject_id=self-registered-user&predicate=onboarding_status&include_superseded=true', {
                      credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
                    })
                  ]);
                  return {active:await activeResponse.json(),history:await historyResponse.json()};
                }""")
                assert len(self_service_state["active"]["items"]) == 1, self_service_state
                assert self_service_state["active"]["items"][0]["value"]["status"] == "verified"
                assert len(self_service_state["history"]["items"]) == 2, self_service_state
                page2.once("dialog", lambda dialog: dialog.accept())
                page2.locator("#forgetSelected").click()
                expect(page2.locator("#mutationNote")).to_contain_text("Soft-forget committed and independently read back for", timeout=15000)
                self_service_after_forget = page2.evaluate("""async () => {
                  const tenant = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_tenant=')).split('=').slice(1).join('='));
                  const [activeResponse, historyResponse] = await Promise.all([
                    fetch('/v1/memories?subject_id=self-registered-user&predicate=onboarding_status', {
                      credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
                    }),
                    fetch('/v1/memories?subject_id=self-registered-user&predicate=onboarding_status&include_superseded=true', {
                      credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
                    })
                  ]);
                  return {active:await activeResponse.json(),history:await historyResponse.json()};
                }""")
                assert self_service_after_forget["active"]["items"] == [], self_service_after_forget
                assert len(self_service_after_forget["history"]["items"]) == 2, self_service_after_forget

                # A valid B session in B's own workspace must not see A's
                # active or superseded claims.
                own_scope_read = page2.evaluate("""async () => {
                  const tenant = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_tenant=')).split('=').slice(1).join('='));
                  const [activeResponse, historyResponse, allResponse] = await Promise.all([
                    fetch('/v1/memories?subject_id=oidc-e2e-user&predicate=preferred_language', {
                      credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
                    }),
                    fetch('/v1/memories?subject_id=oidc-e2e-user&predicate=preferred_language&include_superseded=true', {
                      credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
                    }),
                    fetch('/v1/memories?limit=500&include_superseded=true', {
                      credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
                    })
                  ]);
                  return {active:await activeResponse.json(),history:await historyResponse.json(),
                          all:await allResponse.json()};
                }""")
                assert own_scope_read["active"]["items"] == [], own_scope_read
                assert own_scope_read["history"]["items"] == [], own_scope_read
                assert not any(item.get("subject_id") in {"oidc-e2e-user", "observatory-ui-user"}
                               for item in own_scope_read["all"]["items"]), own_scope_read

                # Directly naming the first user's tenant must not authenticate
                # the second user's otherwise-valid browser Session.
                cross_read = page2.evaluate("""async (tenant) => {
                  const response = await fetch('/v1/memories?limit=100', {
                    credentials:'same-origin', headers:{'X-Memory-Tenant':tenant}
                  });
                  return {status:response.status,body:await response.json()};
                }""", original_tenant)
                assert cross_read["status"] == 401, cross_read

                # The workspace-switch endpoint must reject a tenant that is
                # not present in this principal's live memberships.
                cross_switch = page2.evaluate("""async (tenant) => {
                  const current = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_tenant=')).split('=').slice(1).join('='));
                  const csrf = decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('amc_csrf=')).split('=').slice(1).join('='));
                  const response = await fetch('/v1/identity/select-tenant', {
                    method:'POST', credentials:'same-origin',
                    headers:{'Content-Type':'application/json','X-Memory-Tenant':current,'X-CSRF-Token':csrf},
                    body:JSON.stringify({tenant_id:tenant})
                  });
                  return {status:response.status,body:await response.json()};
                }""", original_tenant)
                assert cross_switch["status"] == 403, cross_switch
                assert not errors2, "Second browser JavaScript errors: " + "; ".join(errors2)
            finally:
                context2.close()

            assert not errors, "Browser JavaScript errors: " + "; ".join(errors)
        finally:
            context.close()
            browser.close()
