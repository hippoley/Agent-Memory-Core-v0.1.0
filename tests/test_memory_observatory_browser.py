"""Opt-in Chromium E2E tests of the committed Memory Observatory HTML.

Run with MEMORY_OBSERVATORY_BROWSER=1. API responses mocked in browser tests;
a separate PostgreSQL integration test validates the actual persisted API.
"""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import pytest

pytestmark=pytest.mark.browser
if os.getenv("MEMORY_OBSERVATORY_BROWSER")!="1":
    pytest.skip("Opt-in Chromium suite",allow_module_level=True)
pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright, expect

PAGE=Path(__file__).resolve().parents[1]/"src/agent_memory_core/resources/dashboard/observatory.html"

@pytest.fixture(scope="module")
def observatory_url():
    data=PAGE.read_bytes()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!="/dashboard/observatory.html":
                self.send_error(404);return
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        yield "http://127.0.0.1:%d/dashboard/observatory.html"%server.server_port
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)

@pytest.fixture
def ui(observatory_url):
    with sync_playwright() as playwright:
        opts={"headless":True}
        if os.getenv("MEMORY_CHROMIUM_PATH"):
            opts["executable_path"]=os.environ["MEMORY_CHROMIUM_PATH"]
        browser=playwright.chromium.launch(**opts)
        context=browser.new_context(viewport={"width":1440,"height":900},accept_downloads=True)
        page=context.new_page()
        errors=[]
        page.on("pageerror",lambda exc: errors.append(str(exc)))
        page.goto(observatory_url,wait_until="load")
        try:
            yield page
            assert not errors,"Browser JS exceptions: "+"; ".join(errors)
        finally:
            context.close();browser.close()

def test_demo_evidence_search_and_replay(ui):
    assert ui.locator("#total").inner_text()=="10"
    assert ui.locator("#pageMark").inner_text()=="/ DEMO"
    ui.locator("#claimPicker").select_option("d-01")
    assert "conversation:21" in ui.locator("#detail").inner_text()
    ui.locator("#query").fill("本地")
    ui.locator("#searchForm button").click()
    assert "local matching claims" in ui.locator("#searchNote").inner_text()
    ui.locator("#reset").click()
    assert "Search currently loaded claims" in ui.locator("#searchNote").inner_text()
    ui.locator("#time").evaluate("el => { el.value = '30'; el.dispatchEvent(new Event('input', {bubbles:true})); }")
    assert int(ui.locator("#total").inner_text())<10
    assert ui.locator("#claimPicker").input_value()==""
    ui.locator("#scope").select_option("3")
    assert int(ui.locator("#total").inner_text())<10
    ui.locator("#pause").click()
    assert ui.locator("#pause").inner_text()=="▶"
    with ui.expect_download() as download:
        ui.locator("#export").click()
    assert download.value.suggested_filename=="memory-observatory-demo.json"

def test_five_step_tour_and_keyboard_focus(ui):
    ui.locator("#tourLaunch").click()
    assert ui.locator("#tour").get_attribute("aria-hidden")=="false"
    for step in range(1,6):
        assert ui.locator("#tourCounter").inner_text()==f"{step:02d} / 05"
        if step==2: assert ui.locator("#scope").input_value()=="0"
        if step==3: assert "conversation:21" in ui.locator("#detail").inner_text()
        if step==4: assert int(ui.locator("#total").inner_text())<10
        ui.locator("#tourNext").click()
    assert ui.locator("#tour").get_attribute("aria-hidden")=="true"
    assert ui.locator("#tourLaunch").evaluate("el=>el===document.activeElement")

def test_live_unauthorized_empty_and_disconnect(ui):
    requests=[]
    def respond(route):
        key=route.request.headers.get("authorization","")
        requests.append(key)
        if key!="Bearer integration-key":
            route.fulfill(status=401,content_type="application/json",body='{"detail":"Unauthorized"}')
        else:
            route.fulfill(status=200,content_type="application/json",body='{"items":[]}')
    ui.route("**/v1/**",respond)
    ui.locator("#connectBtn").click()
    ui.locator("#key").fill("wrong-key")
    ui.locator("#liveBtn").click()
    expect(ui.locator("#authError")).to_contain_text("HTTP 401")
    assert ui.locator("#pageMark").inner_text()=="/ DEMO"
    ui.locator("#key").fill("integration-key")
    ui.locator("#liveBtn").click()
    expect(ui.locator("#pageMark")).to_have_text("/ LIVE")
    expect(ui.locator("#total")).to_have_text("0")
    assert requests.count("Bearer integration-key")==2
    assert ui.locator("#auth").get_attribute("aria-hidden")=="true"
    ui.locator("#connectBtn").click()
    ui.locator("#demoBtn").click()
    assert ui.locator("#total").inner_text()=="10"

def test_escape_returns_focus(ui):
    ui.locator("#connectBtn").click()
    assert ui.locator("#auth").get_attribute("aria-hidden")=="false"
    ui.keyboard.press("Escape")
    assert ui.locator("#auth").get_attribute("aria-hidden")=="true"
    assert ui.locator("#connectBtn").evaluate("el=>el===document.activeElement")

@pytest.mark.parametrize("width,height",[(390,844),(768,1024),(1440,900),(1920,1080)])
def test_viewport_and_screenshot(ui,width,height):
    ui.set_viewport_size({"width":width,"height":height})
    ui.wait_for_timeout(80)
    assert not ui.evaluate("document.documentElement.scrollWidth>innerWidth+2"),f"Overflow at {width}"
    screenshot_dir=os.getenv("MEMORY_OBSERVATORY_SCREENSHOT_DIR")
    if screenshot_dir:
        root=Path(screenshot_dir);root.mkdir(parents=True,exist_ok=True)
        ui.screenshot(path=str(root/f"observatory-{width}x{height}.png"),full_page=True)

def test_live_network_error_is_explicit_and_does_not_replace_demo(ui):
    ui.route("**/v1/**",lambda route: route.abort("failed"))
    ui.locator("#connectBtn").click()
    ui.locator("#key").fill("integration-key")
    ui.locator("#liveBtn").click()
    expect(ui.locator("#authError")).not_to_be_empty()
    assert ui.locator("#pageMark").inner_text()=="/ DEMO"
    assert ui.locator("#total").inner_text()=="10"

def test_large_loaded_page_and_evidence_escape(ui):
    import json
    records=[{"id":"m-%03d"%i,"subject_id":"large-tenant","predicate":"memory_fact",
              "value":{"text":"fact %03d"%i},"source_id":"source:fixture",
              "recorded_at":"2026-11-16T09:00:00Z"} for i in range(500)]
    records[0]["value"]={"text":"<img src=x onerror=alert(1)>"}
    def respond(route):
        data={"items":records} if "/v1/memories?" in route.request.url else {"items":[]}
        route.fulfill(status=200,content_type="application/json",body=json.dumps(data))
    ui.route("**/v1/**",respond)
    ui.locator("#connectBtn").click()
    ui.locator("#key").fill("tenant-key")
    ui.locator("#liveBtn").click()
    expect(ui.locator("#total")).to_have_text("500")
    assert ui.locator("#claimPicker option").count()==501
    ui.locator("#claimPicker").select_option("m-000")
    assert "<img src=x onerror=alert(1)>" in ui.locator("#detail").inner_text()
    assert ui.locator("#detail img").count()==0
