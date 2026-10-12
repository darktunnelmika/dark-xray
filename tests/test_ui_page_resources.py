"""Real Chromium acceptance: no owner-ledger fetch on initial Dashboard/Nodes.

Only a synthetic loopback HTTP server. No production URLs, secrets or database.
"""
import json
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def test_first_dashboard_and_nodes_do_not_await_large_owner_ledgers():
    requests = Counter()
    html = """<!doctype html><html><head></head><body>
    <div id="app"></div><div id="overlay"></div><div id="toasts"></div>
    <script src="/assets/live.js"></script>
    <script src="/assets/ops-v2.js"></script>
    <script src="/assets/overview-v4.js"></script>
    <script src="/assets/ui-stability.js"></script>
    </body></html>"""
    reply = {
        "/api/me": {"id":"dark","role":"owner","csrf":"synthetic","ui":{},
                    "permissions":{},"writes_enabled":True,"version":"test"},
        "/api/clients":[],
        "/api/inbounds":[],
        "/api/sync":{"runtime":{"running":True},"last_poll":1},
        "/api/system":{"engine":{"cpu":16,"xray":{"state":"running"}}},
        "/api/owners":[],
        "/api/resellers":[],
        "/api/nodes":[],
        "/api/operations/overview":{},
        "/api/core/state":{"state":"running"},
        "/api/backup/status":{},
        "/api/audit":[],
        "/api/ip/events":{"events":[],"bans":[]},
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?",1)[0]
            requests[path] += 1
            if path == "/":
                body = html.encode()
                mime = "text/html; charset=utf-8"
            elif path.startswith("/assets/"):
                name = path.removeprefix("/assets/")
                if name not in {"live.js","ops-v2.js","overview-v4.js","ui-stability.js"}:
                    self.send_error(404);return
                body = (ROOT/"web"/name).read_bytes()
                mime = "text/javascript; charset=utf-8"
            elif path in reply:
                if path in ("/api/owners","/api/resellers"):
                    time.sleep(1.8)  # Emulate 9.5m-event ledger aggregates.
                body = json.dumps(reply[path]).encode()
                mime = "application/json"
            else:
                self.send_error(404);return
            self.send_response(200)
            self.send_header("Content-Type",mime)
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
    worker = threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch(headless=True,
                executable_path="/root/.cache/ms-playwright/chromium-1200/chrome-linux64/chrome",
                args=["--no-sandbox"])
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror",lambda error:errors.append(str(error)))
                begin = time.monotonic()
                page.goto(f"http://127.0.0.1:{server.server_port}/",wait_until="domcontentloaded")
                expect(page.locator(".ov4-summary-grid")).to_be_visible(timeout=9000)
                elapsed = time.monotonic()-begin
                assert elapsed < 3, f"Slow first dashboard: {elapsed:.2f}s"
                assert requests["/api/owners"] == requests["/api/resellers"] == 0
                c_before = requests["/api/clients"]
                page.evaluate("go('nodes')")
                assert page.evaluate("state.page")== "nodes"
                expect(page.locator("#content")).not_to_be_empty(timeout=6000)
                assert requests["/api/owners"] == requests["/api/resellers"] == 0
                assert requests["/api/clients"] == c_before
                # Owners/representatives are explicitly fetched on their page.
                page.evaluate("go('resellers')")
                expect(page.locator("#content")).to_contain_text("نمایندگان",timeout=8000)
                assert requests["/api/owners"] >= 1
                assert requests["/api/resellers"] >= 1
                page.evaluate("go('dashboard')")
                expect(page.locator(".ov4-summary-grid")).to_be_visible(timeout=9000)
                assert "نمایندگان" not in errors
                assert not errors, errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()
