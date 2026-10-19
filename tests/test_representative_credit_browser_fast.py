"""Chromium: Representatives always appears, and retries do not double grant.

Synthetic HTTP only; no live DB, payment or secrets.
"""
import json
import os
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT=Path(__file__).resolve().parents[1]
GIB=1024**3


def test_representative_credit_form_fast_and_replay_safe():
    calls=Counter()
    credit_events={}
    rep={"id":"seller","name":"Synthetic Seller","enabled":True,"login_ready":True,
         "allowed":[1],"volume_credit_bytes":5000*GIB,
         "allocated_volume_bytes":5000*GIB,"volume_credit_remaining_bytes":0,
         "unlimited_credit":200,"unlimited_credit_used":0,
         "unlimited_credit_remaining":200,"used_bytes":None,
         "traffic_totals_available":False,"client_count":25}
    html="""<!doctype html><html><head><meta charset="utf-8"></head><body>
    <div id="app"></div><div id="overlay"></div><div id="toasts"></div>
    <script src="/assets/live.js"></script></body></html>"""

    class Handler(BaseHTTPRequestHandler):
        def send(self,status,thing,mime="application/json"):
            data=thing.encode() if isinstance(thing,str) else json.dumps(thing).encode()
            self.send_response(status)
            self.send_header("Content-Type",mime)
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path=self.path.split("?",1)[0]
            calls[path]+=1
            if path=="/":
                self.send(200,html,"text/html; charset=utf-8")
            elif path=="/assets/live.js":
                self.send(200,(ROOT/"web/live.js").read_text(),"application/javascript; charset=utf-8")
            elif path=="/api/me":
                self.send(200,{"id":"dark","role":"owner","csrf":"fixture",
                               "permissions":{},"ui":{},"version":"synthetic",
                               "writes_enabled":True})
            elif path in ("/api/clients","/api/inbounds"):
                self.send(200,[])
            elif path=="/api/sync":
                self.send(200,{"runtime":{"running":True},"items":[]})
            elif path=="/api/system":
                self.send(200,{"engine":{"cpu":1,"xray":{"state":"running"}}})
            elif path=="/api/resellers/quick":
                self.send(200,[rep])
            elif path=="/api/owners/quick":
                self.send(200,[{"id":"seller","name":"Synthetic Seller","allowed":[1]}])
            elif path in ("/api/resellers","/api/owners"):
                # Deliberately fail: an old full-ledger endpoint must never run.
                self.send(500,{"detail":"Historical ledger heavy path was requested"})
            else:
                self.send(404,{"detail":"Unknown synthetic route"})

        def do_POST(self):
            path=self.path.split("?",1)[0]
            calls[path]+=1
            content=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if path=="/api/resellers/seller/credits":
                event=content["event_id"]
                if event not in credit_events:
                    credit_events[event]=content
                    rep["volume_credit_bytes"]+=content["volume_bytes"]
                    rep["volume_credit_remaining_bytes"]+=content["volume_bytes"]
                    # Emulate response loss AFTER the durable credit grant.
                    self.send(503,{"detail":"Injected lost response"})
                else:
                    assert credit_events[event]==content
                    self.send(200,{"recorded":False,"representative":rep})
            else:
                self.send(404,{"detail":"Unknown synthetic mutation"})

        def log_message(self,*_):pass

    srv=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    worker=threading.Thread(target=srv.serve_forever,daemon=True)
    worker.start()
    try:
        with sync_playwright() as playwright:
            launch={"headless":True,"args":["--no-sandbox"]}
            if os.environ.get("DARK_TEST_CHROMIUM"):
                launch["executable_path"]=os.environ["DARK_TEST_CHROMIUM"]
            browser=playwright.chromium.launch(**launch)
            try:
                page=browser.new_page(viewport={"width":1120,"height":820})
                errors=[];page.on("pageerror",lambda exc:errors.append(str(exc)))
                page.goto(f"http://127.0.0.1:{srv.server_port}/",wait_until="domcontentloaded")
                nav=page.locator('.nav-btn[data-page="resellers"]')
                expect(nav).to_be_visible(timeout=10000)
                # Hold route hydration so loading-state assertions cannot race
                # a fast localhost response or a newly rebuilt page shell.
                page.evaluate("""() => {
                    const original = load; let held = false;
                    load = async function() {
                        if (!held && state.page === 'resellers') {
                            held = true;
                            await new Promise(resolve => { window.releaseRouteLoad = resolve; });
                        }
                        return original();
                    };
                }""")
                nav.click()
                expect(page.locator("#content")).to_have_attribute("aria-busy","true")
                page.evaluate("() => window.releaseRouteLoad()")
                expect(page.locator("#content")).to_contain_text("Synthetic Seller",timeout=10000)
                expect(page.locator("#content")).not_to_have_attribute("aria-busy","true")
                assert calls["/api/resellers/quick"]>=1
                assert calls["/api/resellers"]==calls["/api/owners"]==0

                credit=page.locator('[data-act="resourcecredit"][data-id="seller"]')
                expect(credit).to_be_visible()
                credit.click()
                form=page.locator("#dialog-form")
                expect(form).to_be_visible()
                form.locator('[name="volume_delta"]').fill("20")
                form.locator('[name="unlimited_delta"]').fill("0")
                form.locator('button[type="submit"]').click()
                expect(form.locator("#form-error")).to_contain_text("Injected lost response",timeout=10000)
                # Form remains open; retry reuses its exact event ID.
                form.locator('button[type="submit"]').click()
                expect(form).to_have_count(0,timeout=10000)
                assert len(credit_events)==1
                assert len(next(iter(credit_events)))>=16
                assert calls["/api/resellers/seller/credits"]==2
                assert rep["volume_credit_remaining_bytes"]==20*GIB
                assert not errors,errors
            finally:
                browser.close()
    finally:
        srv.shutdown();worker.join(timeout=3);srv.server_close()
