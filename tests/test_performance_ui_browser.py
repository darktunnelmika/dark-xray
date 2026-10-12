"""Real Chromium regression for global DARK XRAY UI, isolated from production.

The synthetic localhost API is read-only. No credentials, database, services,
customer nodes, or live URLs are used.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    import pytest
    pytest.skip("Optional real Chromium suite requires Playwright", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
CHROME = "/root/.cache/ms-playwright/chromium-1200/chrome-linux64/chrome"

SUMMARY = {
    "read_only": True, "freshness": "fresh", "generated_at": 1720000000,
    "clients": {"total": 747, "policy_eligible": 730, "policy_blocked": 17,
                "current_stored_usage_bytes": 1024**3,
                "usage_scope": "current-stored-counters-not-lifetime-ledger"},
    "representatives": {"total": 3, "enabled": 2},
    "inbounds": {"total": 8, "enabled": 7},
    "nodes": {"total": 5, "enabled": 5, "recent_heartbeat": 5,
              "status_source": "cached-heartbeat-only-not-route-readiness"},
}


def response_for(route):
    api = {
        "/api/me": {"id": "dark", "role": "owner", "csrf": "synthetic",
                    "permissions": {}, "writes_enabled": False,
                    "ui": {"title": "DARK XRAY QA"}, "version": "0.10.0-qa"},
        "/api/dashboard/summary": SUMMARY,
        "/api/clients": [],
        "/api/inbounds": [],
        "/api/owners": [{"id": "dark", "name": "Test owner"}],
        "/api/resellers": [],
        "/api/groups": [],
        "/api/sync": {"runtime": {"running": True, "dirty": False},
                      "items": [], "last_poll": 1720000000},
        "/api/system": {"engine": {
            "cpu": 12.5, "mem": {"current": 1, "total": 2},
            "xray": {"state": "running", "mem": 100},
            "netIO": {"up": 0, "down": 0},
            "connections": {"available": True, "open": 0, "tcp": 0, "udp": 0}}},
        "/api/nodes": [],
        "/api/operations/overview": {},
        "/api/core/state": {"running": True, "dirty": False},
        "/api/core/config": {},
        "/api/update/status": {"state": "idle", "current": {"version": "qa"}},
        "/api/backup/status": {"ready": True},
        "/api/audit": [],
        "/api/ip/events": {"events": [], "bans": []},
        "/api/security-center": {},
        "/api/ledger/credits": [],
        "/api/ledger/traffic": [],
        "/api/auth/sessions": [],
        "/api/keys": [],
        "/api/settings/panel": {"value": {}},
    }
    if route.startswith("/api/settings/"):
        return {"value": {}}
    return api.get(route)


def serve_mock(*, delayed=None):
    requests = Counter()
    started = []
    delayed = delayed or {}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            uri = urlsplit(self.path)
            route = uri.path
            requests[route] += 1
            started.append((route, time.monotonic()))
            if route in delayed:
                time.sleep(delayed[route])
            if route == "/":
                blob = (ROOT / "web/index.html").read_bytes()
                media = "text/html; charset=utf-8"
                status = 200
            elif route.startswith("/assets/"):
                rel = unquote(route[len("/assets/"):])
                asset = (ROOT / "web" / rel).resolve()
                if not asset.is_relative_to((ROOT / "web").resolve()) or not asset.is_file():
                    self.send_error(404)
                    return
                blob = asset.read_bytes()
                ext = asset.suffix
                media = {".css": "text/css", ".js": "application/javascript",
                         ".woff2": "font/woff2", ".svg": "image/svg+xml"}.get(ext, "application/octet-stream")
                status = 200
            elif route == "/favicon.ico":
                blob, media, status = b"", "image/x-icon", 200
            else:
                value = response_for(route)
                status = 200 if value is not None else 404
                blob = json.dumps(value if value is not None else {"detail": "Synthetic endpoint unavailable"}).encode()
                media = "application/json; charset=utf-8"
            try:
                self.send_response(status)
                self.send_header("Content-Type", media)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, requests, started


def get_browser(play):
    # Production diagnostic host and GitHub CI store Playwright in different
    # cache locations. Use the installed Playwright browser on CI.
    options = {"headless": True, "args": ["--no-sandbox"]}
    if Path(CHROME).is_file():
        options["executable_path"] = CHROME
    return play.chromium.launch(**options)


def ready_page(page, route, timeout=12000):
    page.wait_for_function(
        """route => {
            const el=document.querySelector('#content');
            return typeof state==='object' && state.page===route &&
                   el && el.textContent.length > 20 && el.getAttribute('aria-busy')!=='true';
        }""", arg=route, timeout=timeout)


def test_real_chromium_dashboard_navigation_mobile_and_language():
    server, worker, calls, started = serve_mock()
    try:
        with sync_playwright() as play:
            browser = get_browser(play)
            try:
                page = browser.new_page(viewport={"width": 375, "height": 812})
                errors = []
                page.on("pageerror", lambda err: errors.append(str(err)))
                started_at = time.monotonic()
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
                page.locator(".ov4-summary-grid").wait_for(state="visible", timeout=10000)
                first_paint = time.monotonic() - started_at
                assert first_paint < 6, f"dashboard too slow: {first_paint:.2f}s"
                assert calls["/api/owners"] == calls["/api/resellers"] == 0
                assert calls["/api/clients"] == 0, "Legacy eager loader raced FastPath at boot"
                for route in ("nodes", "clients", "inbounds", "sync", "dashboard"):
                    page.evaluate("(route) => { void go(route); }", route)
                    ready_page(page, route)
                    # The shell must not introduce body-level sideways scrolling.
                    widths = page.evaluate("() => ({doc:document.documentElement.scrollWidth, viewport:innerWidth})")
                    assert widths["doc"] <= widths["viewport"] + 3, (route, widths)
                assert calls["/api/resellers"] == 0
                assert calls["/api/owners"] >= 1
                page.reload(wait_until="domcontentloaded")
                ready_page(page, "dashboard")
                assert errors == [], errors
                print(f"QA first dashboard paint: {first_paint:.3f}s / API requests: {dict((k,v) for k,v in calls.items() if k.startswith('/api/'))}")
                page.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()


def test_slow_owner_ledger_does_not_trap_unrelated_navigation():
    server, worker, calls, started = serve_mock(delayed={"/api/owners": 16})
    try:
        with sync_playwright() as play:
            browser = get_browser(play)
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda err: errors.append(str(err)))
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
                ready_page(page, "dashboard")
                page.evaluate("() => { void go('clients'); }")
                page.wait_for_function("() => state.page === 'clients' && document.querySelector('#content')?.getAttribute('aria-busy')==='true'")
                started_at = time.monotonic()
                page.evaluate("() => { void go('nodes'); }")
                ready_page(page, "nodes", timeout=6000)
                elapsed = time.monotonic() - started_at
                assert elapsed < 2, f"Nodes navigation blocked behind owners: {elapsed:.3f}s"
                assert calls["/api/resellers"] == 0
                assert errors == [], errors
                print(f"QA switch away from blocked Clients: {elapsed:.3f}s")
                page.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()


def test_slow_owner_ledger_has_bounded_page_loading_and_retry_backoff():
    server, worker, calls, started = serve_mock(delayed={"/api/owners": 20})
    try:
        with sync_playwright() as play:
            browser = get_browser(play)
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda err: errors.append(str(err)))
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
                ready_page(page, "dashboard")
                before = time.monotonic()
                page.evaluate("() => { void go('clients'); }")
                ready_page(page, "clients", timeout=9000)
                elapsed = time.monotonic() - before
                assert elapsed < 7.8, f"owner ledger trapped Clients for {elapsed:.2f}s"
                observed = page.evaluate("() => state.errors.owners || ''")
                assert "timed out" in observed, observed
                assert "timed out" in page.locator("#content").inner_text()
                assert calls["/api/owners"] == 1
                # Revisiting the same Clients route does not rescan the giant
                # owner ledger every time an operator switches menus.
                page.evaluate("() => { void go('nodes'); }")
                ready_page(page, "nodes")
                page.evaluate("() => { void go('clients'); }")
                ready_page(page, "clients", timeout=6000)
                assert calls["/api/owners"] == 1
                assert errors == [], errors
                print(f"QA bounded slow owner ledger: {elapsed:.3f}s")
                page.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()


def test_pending_navigation_persists_route_without_duplicate_client_reads():
    server, worker, calls, started = serve_mock(delayed={"/api/clients": 2})
    try:
        with sync_playwright() as play:
            browser = get_browser(play)
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda err: errors.append(str(err)))
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
                ready_page(page, "dashboard")
                page.evaluate("() => { void go('clients'); void go('clients'); void renderPage(); }")
                page.wait_for_function("() => state.page==='clients' && document.querySelector('#content')?.getAttribute('aria-busy')==='true'")
                saved = page.evaluate("() => sessionStorage.getItem('dark_last_page')")
                assert saved == "clients", f"route was not saved until after a slow fetch: {saved!r}"
                assert "Loading current page data" in page.locator("#content").inner_text()
                ready_page(page, "clients", timeout=7000)
                assert calls["/api/clients"] == 1, f"duplicate clients reads: {calls}"
                page.reload(wait_until="domcontentloaded")
                ready_page(page, "clients", timeout=7000)
                assert errors == [], errors
                page.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()


def test_mobile_breakpoints_and_language_round_trip_keep_current_page():
    server, worker, calls, started = serve_mock()
    try:
        with sync_playwright() as play:
            browser = get_browser(play)
            try:
                page = browser.new_page(viewport={"width": 360, "height": 780})
                errors = []
                page.on("pageerror", lambda err: errors.append(str(err)))
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
                ready_page(page, "dashboard")
                page.evaluate("() => { void go('clients'); }")
                ready_page(page, "clients")
                for width in (320, 360, 375, 390, 430, 760, 768, 1024, 1440):
                    page.set_viewport_size({"width": width, "height": 780})
                    data = page.evaluate("() => ({sw:document.documentElement.scrollWidth, iw:innerWidth})")
                    assert data["sw"] <= data["iw"] + 3, (width, data)
                page.set_viewport_size({"width": 390, "height": 780})
                page.locator(".cyber-lang-switch").click()
                # A click schedules location.reload(); first await the *new*
                # document rather than accepting the previous page as ready.
                page.wait_for_function("document.documentElement.lang==='fa'", timeout=10000)
                ready_page(page, "clients", timeout=10000)
                assert page.evaluate("document.documentElement.dir") == "rtl"
                assert page.evaluate("sessionStorage.getItem('dark_last_page')") == "clients"
                page.locator(".cyber-lang-switch").click()
                page.wait_for_function("document.documentElement.lang==='en'", timeout=10000)
                ready_page(page, "clients", timeout=10000)
                assert page.evaluate("document.documentElement.dir") == "ltr"
                assert errors == [], errors
                page.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()
