import re
from pathlib import Path

from fastapi.testclient import TestClient

from auth import Auth
from core import Config,CoreEngine
from dark_policy import Actor,Store
from manager import Manager
from server import make_app

ROOT=Path(__file__).resolve().parents[1]


def app_env(tmp_path,panel_path='/dark-admin'):
    store=Store(tmp_path/'dark.sqlite3')
    config=Config(xray_binary=str(tmp_path/'missing-xray'),xray_assets=str(tmp_path),panel_path=panel_path,test_engine=True)
    engine=CoreEngine(config,store,tmp_path/'runtime');manager=Manager(store,engine);auth=Auth(store,tmp_path/'secret.key')
    auth.bootstrap('dark','ContractPass88');manager.owner_put(Actor('dark','owner',{}),'dark',name='DARK',allowed=[])
    return store,make_app(manager,auth,background=False),config


def active_scripts():
    html=(ROOT/'web/index.html').read_text(encoding='utf-8')
    return [ROOT/'web'/name for name in re.findall(r'<script[^>]+src="assets/([^"?]+\.js)"',html)]


def test_every_index_asset_loads_under_panel_prefix(tmp_path):
    store,app,config=app_env(tmp_path)
    try:
        with TestClient(app,base_url=config.public_origin) as c:
            r=c.get('/dark-admin/');assert r.status_code==200
            refs=re.findall(r'(?:src|href)="(assets/[^"?]+)"',r.text)
            assert refs and len(refs)==len(set(refs))
            for ref in refs:
                got=c.get('/dark-admin/'+ref)
                assert got.status_code==200,(ref,got.status_code)
            assert c.get('/assets/style.css').status_code==404
    finally:store.close()


def test_active_web_api_literals_have_backend_route_prefix(tmp_path):
    store,app,_=app_env(tmp_path,'/')
    try:
        routes={getattr(r,'path','') for r in app.routes}
        dynamic_prefixes={r.split('{',1)[0] for r in routes if '{' in r}
        literals=set()
        for path in active_scripts():
            text=path.read_text(encoding='utf-8')
            literals.update(re.findall(r"(?:api|appUrl)\(\s*['\"](/api/[^'\"]*)['\"]",text))
        assert literals
        missing=[]
        for literal in sorted(literals):
            route=literal.split('?',1)[0]
            if route in routes:continue
            if any(route.startswith(p) or p.startswith(route) for p in dynamic_prefixes):continue
            missing.append(route)
        assert not missing,'Frontend API literals without backend route coverage: '+', '.join(missing)
    finally:store.close()


def test_active_web_never_bypasses_panel_base_for_direct_fetch_or_links():
    html=(ROOT/'web/index.html').read_text(encoding='utf-8')
    assert not re.search(r'(?:src|href)="/(?:assets|api)/',html)
    for path in active_scripts():
        text=path.read_text(encoding='utf-8')
        assert not re.search(r"\bfetch\(\s*['\"]/(?:api|assets)/",text),path.name
        assert not re.search(r'(?:href|src)=\\?["\']/(?:api|assets)/',text),path.name
