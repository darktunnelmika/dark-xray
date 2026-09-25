#!/usr/bin/env python3
import json,shutil,tempfile,threading
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"qa";OUT.mkdir(exist_ok=True)
DATA={"title":"DARK XRAY","client":"portal-browser","url":"https://vpn.test/sub/ABCDEFGHIJKLMNOP","used":2*1024**3,"total":10*1024**3,"expiry":0,"update_hours":6,"announce":"DARK browser acceptance"}

class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args): pass

def main():
    with tempfile.TemporaryDirectory(prefix="dark-sub-portal.") as td:
        root=Path(td);shutil.copytree(ROOT/"web",root/"assets")
        page=(ROOT/"web/sub-portal.html").read_text(encoding="utf-8").replace("__SUB_DATA__",json.dumps(DATA,separators=(",",":")))
        (root/"index.html").write_text(page,encoding="utf-8")
        handler=lambda *a,**kw: Quiet(*a,directory=str(root),**kw)
        httpd=ThreadingHTTPServer(("127.0.0.1",0),handler);port=httpd.server_port
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        cases=[
            ("android",{"width":412,"height":915},"Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/154 Mobile Safari/537.36",3),
            ("ios",{"width":390,"height":844},"Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 Version/19.0 Mobile/15E148 Safari/604.1",3),
            ("windows",{"width":1440,"height":900},"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36",2),
        ]
        report=[]
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            for name,viewport,ua,count in cases:
                ctx=browser.new_context(viewport=viewport,user_agent=ua)
                pg=ctx.new_page();external=[]
                local_prefix=f"http://127.0.0.1:{port}"
                def watch(req):
                    if not req.url.startswith(local_prefix) and not req.url.startswith("data:"):
                        external.append(req.url)
                pg.on("request",watch)
                pg.goto(local_prefix+"/index.html",wait_until="networkidle")
                pg.wait_for_timeout(100)
                tab=pg.locator(f'[data-platform-tab="{name}"]')
                assert "active" in (tab.get_attribute("class") or ""),name
                active=pg.locator(f'[data-platform-panel="{name}"].active .app-card')
                assert active.count()==count,(name,active.count())
                overflow=pg.evaluate("document.documentElement.scrollWidth-window.innerWidth")
                assert overflow<=1,(name,overflow)
                icons=pg.locator(f'[data-platform-panel="{name}"].active .app-icon img')
                assert icons.count()==count,(name,icons.count())
                for i in range(count):
                    assert icons.nth(i).evaluate("img=>img.complete&&img.naturalWidth>0"),(name,i)
                buttons=pg.locator(f'[data-platform-panel="{name}"].active .app-import')
                heights=[]
                for i in range(buttons.count()):
                    box=buttons.nth(i).bounding_box();assert box
                    heights.append(box["height"])
                if name!="windows":
                    assert min(heights)>=44,(name,heights)
                assert not external,(name,external)
                shot=OUT/f"subscription-portal-{name}.png"
                pg.screenshot(path=str(shot),full_page=True)
                report.append({"platform":name,"cards":count,"overflow_px":overflow,"min_connect_height":min(heights),"external_requests":external})
                ctx.close()
            browser.close()
        httpd.shutdown()
        (OUT/"subscription-portal-browser.json").write_text(json.dumps({"passed":True,"cases":report},indent=2),encoding="utf-8")
        print("SUBSCRIPTION_PORTAL_BROWSER=PASS")

if __name__=="__main__":
    main()
