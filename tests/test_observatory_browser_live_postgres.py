"""Real browser -> listening Uvicorn -> real PostgreSQL Observatory acceptance.

Unlike the static browser suite, no route is intercepted. All memory/event
responses originate at the actual FastAPI process and canonical PostgreSQL.
The database failure case explicitly injects a store-connection failure; it
does NOT claim to simulate a physical PostgreSQL crash/restart.
"""
import json
import os
import socket
import time
from datetime import datetime, timezone
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.integration]
if os.getenv("MEMORY_OBSERVATORY_LIVE_BROWSER") != "1":
    pytest.skip("Opt-in real Uvicorn/PostgreSQL/Chromium acceptance", allow_module_level=True)

pytest.importorskip("playwright.sync_api")
pytest.importorskip("psycopg")
pytest.importorskip("uvicorn")
from playwright.sync_api import sync_playwright, expect
from agent_memory_core.api import create_app
from agent_memory_core.postgres import PostgresMemory
import uvicorn


def http_json(url, *, token=None, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=10) as response:
        return response.status, json.load(response)


@pytest.fixture(scope="module")
def runtime():
    dsn = os.environ.get("MEMORY_TEST_DSN")
    if not dsn:
        pytest.fail("MEMORY_TEST_DSN required for real Live browser acceptance")
    db = PostgresMemory(dsn)
    try:
        db.migrate()
    finally:
        db.close()
    tenant = "observatory-browser-" + uuid4().hex
    foreign_tenant = "observatory-foreign-" + uuid4().hex
    key = "observatory-live-browser-test-token-with-32-chars"
    broken = {"value": False}

    def store_factory():
        if broken["value"]:
            raise ConnectionError("Injected store connection failure for browser test")
        return PostgresMemory(dsn)

    app = create_app(api_key=key, tenant_id=tenant, store_factory=store_factory)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="error", access_log=False)
    server = uvicorn.Server(config)
    worker = Thread(target=server.run, daemon=True)
    worker.start()
    base = "http://127.0.0.1:" + str(port)
    try:
        for _ in range(100):
            if not worker.is_alive():
                pytest.fail("Uvicorn stopped before it became ready")
            try:
                status, body = http_json(base + "/v1/health")
                if status == 200 and body.get("status") == "alive":
                    break
            except (URLError, OSError):
                pass
            time.sleep(.1)
        else:
            pytest.fail("Actual listening Uvicorn HTTP server did not become ready")
        yield {"base": base, "key": key, "tenant": tenant, "other": foreign_tenant,
               "dsn": dsn, "broken": broken}
    finally:
        broken["value"] = False
        server.should_exit = True
        worker.join(timeout=12)
        assert not worker.is_alive(), "Uvicorn thread did not shut down cleanly"


def test_real_live_browser_uses_its_own_persisted_tenant_history(runtime):
    base, key = runtime["base"], runtime["key"]
    # No browser request mocking anywhere in this test.
    with pytest.raises(HTTPError) as no_auth:
        http_json(base + "/v1/memories?limit=500&include_superseded=true")
    assert no_auth.value.code == 401
    with pytest.raises(HTTPError) as wrong_auth:
        http_json(base + "/v1/memories?limit=500&include_superseded=true",
                  token="wrong-key")
    assert wrong_auth.value.code == 403

    old = {"subject_id": "supplier-unique-" + uuid4().hex,
           "predicate": "settlement_period",
           "value": {"months": 1}, "source_id": "evidence:old",
           "valid_from": "2026-10-01T00:00:00Z"}
    status, first = http_json(base + "/v1/memories", token=key, method="POST",
                              payload=old)
    assert status == 201
    status, second = http_json(base + "/v1/memories", token=key, method="POST",
                               payload={**old, "value": {"months": 3},
                                        "source_id": "evidence:new",
                                        "valid_from": "2026-11-01T00:00:00Z",
                                        "replaces_id": first["id"]})
    assert status == 201
    assert first["id"] != second["id"]

    other = PostgresMemory(runtime["dsn"])
    try:
        other.remember(tenant_id=runtime["other"],
                       subject_id=old["subject_id"],
                       predicate=old["predicate"],
                       value={"months": 999},
                       source_id="evidence:foreign",
                       valid_from=datetime(2026, 10, 1, tzinfo=timezone.utc))
    finally:
        other.close()

    # Direct SQL verifies persistence independently of HTTP and canvas.
    sql = PostgresMemory(runtime["dsn"])
    try:
        rows = sql.conn.execute(
            "SELECT id::text, source_id, superseded_at FROM memory_claims "
            "WHERE tenant_id=%s AND subject_id=%s ORDER BY recorded_at",
            (runtime["tenant"], old["subject_id"])).fetchall()
        assert len(rows) == 2
        assert {row[0] for row in rows} == {first["id"], second["id"]}
        assert any(row[0] == first["id"] and row[2] is not None for row in rows)
    finally:
        sql.close()

    with sync_playwright() as browser_api:
        browser = browser_api.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        errors, requests = [], []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.on("request", lambda req: requests.append(req.url) if "/v1/" in req.url else None)
        try:
            page.goto(base + "/dashboard/observatory.html", wait_until="load")
            expect(page.locator("#pageMark")).to_have_text("/ DEMO")
            page.locator("#connectBtn").click()
            page.locator("#key").fill("wrong-key")
            page.locator("#liveBtn").click()
            expect(page.locator("#authError")).to_contain_text("HTTP 403")
            expect(page.locator("#pageMark")).to_have_text("/ DEMO")

            page.locator("#key").fill(key)
            page.locator("#liveBtn").click()
            expect(page.locator("#pageMark")).to_have_text("/ LIVE")
            expect(page.locator("#total")).to_have_text("2")
            expect(page.locator("#active")).to_have_text("1")
            expect(page.locator("#old")).to_have_text("1")
            assert int(page.locator("#eventsCount").inner_text()) >= 2
            page.locator("#claimPicker").select_option(first["id"])
            assert "evidence:old" in page.locator("#detail").inner_text()
            page.locator("#claimPicker").select_option(second["id"])
            assert "evidence:new" in page.locator("#detail").inner_text()
            assert "999" not in page.locator("#detail").inner_text()
            assert all(url.startswith(base + "/v1/") for url in requests)
            assert any("/v1/memories?" in url for url in requests)
            assert any("/v1/events?" in url for url in requests)

            page.reload(wait_until="load")
            expect(page.locator("#pageMark")).to_have_text("/ DEMO")
            page.locator("#connectBtn").click()
            page.locator("#key").fill(key)
            page.locator("#liveBtn").click()
            expect(page.locator("#pageMark")).to_have_text("/ LIVE")
            expect(page.locator("#total")).to_have_text("2")
            page.locator("#claimPicker").select_option(second["id"])
            assert "evidence:new" in page.locator("#detail").inner_text()
            assert not errors, errors
        finally:
            context.close()
            browser.close()


def test_database_connection_failure_is_visible_without_false_live_success(runtime):
    base, key = runtime["base"], runtime["key"]
    with sync_playwright() as browser_api:
        browser = browser_api.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        try:
            page.goto(base + "/dashboard/observatory.html", wait_until="load")
            runtime["broken"]["value"] = True
            page.locator("#connectBtn").click()
            page.locator("#key").fill(key)
            page.locator("#liveBtn").click()
            expect(page.locator("#authError")).to_contain_text("HTTP 500")
            expect(page.locator("#pageMark")).to_have_text("/ DEMO")
            expect(page.locator("#total")).to_have_text("10")
            assert not errors, errors
        finally:
            runtime["broken"]["value"] = False
            browser.close()


def test_live_browser_create_revision_and_soft_forget_against_postgres(runtime):
    """Browser mutations must hit the real API and canonical PostgreSQL, no route mocks."""
    base, key = runtime["base"], runtime["key"]
    subject = "observatory-ui-mutation-" + uuid4().hex
    with sync_playwright() as browser_api:
        browser = browser_api.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.on("dialog", lambda dialog: dialog.accept())
        try:
            page.goto(base + "/dashboard/observatory.html", wait_until="load")
            page.locator("#connectBtn").click()
            page.locator("#key").fill(key)
            page.locator("#liveBtn").click()
            expect(page.locator("#pageMark")).to_have_text("/ LIVE")
            expect(page.locator("#writePanel")).to_be_visible()

            page.locator("#writeSubject").fill(subject)
            page.locator("#writePredicate").fill("settlement_period")
            page.locator("#writeValue").fill('{"months":1}')
            page.locator("#writeSource").fill("ui-test:original")
            page.locator("#saveMemory").click()
            expect(page.locator("#mutationNote")).to_contain_text("Memory created. New ID:")
            first_id = page.locator("#detail .provenance").nth(0).inner_text()
            assert first_id

            page.locator("#targetSelected").click()
            page.locator("#writeValue").fill('{"months":3}')
            page.locator("#writeSource").fill("ui-test:correction")
            page.locator("#saveMemory").click()
            expect(page.locator("#mutationNote")).to_contain_text("Revision committed.")
            expect(page.locator("#total")).to_have_text("2")
            expect(page.locator("#active")).to_have_text("1")
            expect(page.locator("#old")).to_have_text("1")
            second_id = page.locator("#detail .provenance").nth(0).inner_text()
            assert second_id and second_id != first_id

            page.locator("#forgetSelected").click()
            expect(page.locator("#mutationNote")).to_contain_text("Soft-forget committed")
            expect(page.locator("#total")).to_have_text("2")
            expect(page.locator("#active")).to_have_text("0")
            expect(page.locator("#old")).to_have_text("2")

            # Independent SQL assertion: original superseded, replacement forgotten;
            # both rows remain auditable and no active fact remains.
            sql = PostgresMemory(runtime["dsn"])
            try:
                rows = sql.conn.execute(
                    "SELECT id::text, superseded_at, forgotten_at, source_id "
                    "FROM memory_claims WHERE tenant_id=%s AND subject_id=%s "
                    "ORDER BY recorded_at",
                    (runtime["tenant"], subject)).fetchall()
                assert len(rows) == 2, rows
                by_source = {row[3]: row for row in rows}
                assert by_source["ui-test:original"][1] is not None
                assert by_source["ui-test:correction"][2] is not None
                assert all(row[1] is not None or row[2] is not None for row in rows)
            finally:
                sql.close()
            assert not errors, errors
        finally:
            context.close()
            browser.close()
