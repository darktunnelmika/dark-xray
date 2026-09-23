import importlib.util
import json
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


def load_fetch_core():
    path=ROOT/'tools'/'fetch-core.py'
    spec=importlib.util.spec_from_file_location('dark_fetch_core_tests',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod


def test_project_pins_official_xray_26327_digests_without_api(monkeypatch):
    mod=load_fetch_core()
    monkeypatch.setattr(mod,'get',lambda *a,**k:(_ for _ in ()).throw(AssertionError('pinned release must not call metadata API')))
    amd=mod.release_info('v26.3.27','Xray-linux-64.zip')
    arm=mod.release_info('v26.3.27','Xray-linux-arm64-v8a.zip')
    assert amd['expected']=='23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae'
    assert arm['expected']=='4d30283ae614e3057f730f67cd088a42be6fdf91f8639d82cb69e48cde80413c'
    assert amd['source']==arm['source']=='pinned-project-digest'


def test_unpinned_release_uses_api_digest(monkeypatch):
    mod=load_fetch_core();seen=[]
    payload={'draft':False,'prerelease':False,'assets':[{
        'name':'Xray-linux-64.zip','digest':'sha256:'+'a'*64,
        'browser_download_url':'https://github.com/XTLS/Xray-core/releases/download/v99.1.2/Xray-linux-64.zip'}]}
    def get(url,limit,github_token=''):
        seen.append((url,github_token));return json.dumps(payload).encode()
    monkeypatch.setattr(mod,'get',get)
    info=mod.release_info('v99.1.2','Xray-linux-64.zip','temporary-token')
    assert info['expected']=='a'*64 and info['source']=='github-release-api'
    assert seen==[('https://api.github.com/repos/XTLS/Xray-core/releases/tags/v99.1.2','temporary-token')]


def test_github_token_is_sent_only_to_api_host(monkeypatch):
    mod=load_fetch_core();headers=[]
    class Response:
        def __init__(self,url):self.url=url;self.done=False
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def read(self,n):
            if self.done:return b''
            self.done=True;return b'{}'
    def urlopen(req,timeout=0):
        headers.append((req.full_url,req.get_header('Authorization')))
        return Response(req.full_url)
    monkeypatch.setattr(mod,'urlopen',urlopen)
    mod.get('https://api.github.com/repos/XTLS/Xray-core/releases/tags/v1.2.3',1024,'secret')
    mod.get('https://github.com/XTLS/Xray-core/releases/download/v1.2.3/Xray-linux-64.zip',1024,'secret')
    assert headers[0][1]=='Bearer secret'
    assert headers[1][1] is None
