#!/usr/bin/env python3
"""Download a pinned official Xray archive, verify GitHub's SHA-256, then install.

HTTPS verification is never disabled. No mirror and no downloaded shell script
is executed. A release tag is explicit; `latest` is not accepted for a build.
"""
import argparse, hashlib, json, os, platform, re, subprocess, sys, tempfile
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urlsplit

MAX=256*1024*1024

def get(url, limit):
    req=Request(url,headers={'User-Agent':'DARK-XRAY-build/0.6','Accept':'application/vnd.github+json'})
    with urlopen(req,timeout=45) as response:
        final=urlsplit(response.url)
        if final.scheme!='https' or not (final.hostname in {'api.github.com','github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}):
            raise ValueError('Untrusted download redirect')
        chunks=[];size=0
        while True:
            chunk=response.read(1024*1024)
            if not chunk:break
            size+=len(chunk)
            if size>limit:raise ValueError('Download exceeded size limit')
            chunks.append(chunk)
    return b''.join(chunks)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--version',default='v26.3.27')
    p.add_argument('--destination',type=Path,required=True)
    a=p.parse_args()
    if not re.fullmatch(r'v\d+\.\d+\.\d+',a.version):raise SystemExit('Use an explicit release tag')
    arch={'x86_64':'64','amd64':'64','aarch64':'arm64-v8a','arm64':'arm64-v8a'}.get(platform.machine().lower())
    if sys.platform!='linux' or not arch:raise SystemExit('Only Linux amd64/arm64 are supported by this helper')
    name='Xray-linux-'+arch+'.zip'
    try:
        meta=json.loads(get('https://api.github.com/repos/XTLS/Xray-core/releases/tags/'+a.version,3*1024*1024))
        if meta.get('draft') or meta.get('prerelease'):raise ValueError('This helper does not auto-install draft/prerelease builds')
        asset=next(x for x in meta['assets'] if x['name']==name)
        expected=asset.get('digest','')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}',expected):raise ValueError('Official SHA-256 metadata missing; use offline import with independently obtained hash')
        expected=expected[7:]
        url=asset['browser_download_url']
        if url!='https://github.com/XTLS/Xray-core/releases/download/'+a.version+'/'+name:raise ValueError('Unexpected official asset URL')
        raw=get(url,MAX)
        if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('SHA-256 mismatch')
        with tempfile.TemporaryDirectory() as tmp:
            z=Path(tmp)/name;z.write_bytes(raw)
            subprocess.run([sys.executable,str(Path(__file__).with_name('import-core.py')),'--archive',str(z),
                            '--sha256',expected,'--destination',str(a.destination)],check=True)
        record={'tag':a.version,'asset':name,'archive_sha256':expected,'origin':url,
                'binary_sha256':hashlib.sha256((a.destination/'xray').read_bytes()).hexdigest()}
        (a.destination/'DARK-CORE-PROVENANCE.json').write_text(json.dumps(record,indent=2))
        print(json.dumps(record,indent=2))
    except Exception as exc:raise SystemExit('Core download/install failed (no fallback to insecure download): '+str(exc))
if __name__=='__main__':main()
