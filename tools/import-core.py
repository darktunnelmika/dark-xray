#!/usr/bin/env python3
"""Install an administrator-provided official Xray archive after SHA-256 verification.
No network request and no remote installer execution. Existing installations are not overwritten.
"""
import argparse,hashlib,os,re,zipfile
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--archive',type=Path,required=True)
p.add_argument('--sha256',required=True,help='Expected official archive SHA-256 obtained independently')
p.add_argument('--destination',type=Path,required=True)
a=p.parse_args()
if not re.fullmatch('[a-fA-F0-9]{64}',a.sha256):raise SystemExit('Expected a 64-character SHA-256')
if not a.archive.is_file() or a.archive.is_symlink() or a.archive.stat().st_size>256*1024*1024:raise SystemExit('Archive must be a regular file under 256 MiB')
h=hashlib.sha256()
with a.archive.open('rb') as f:
    for data in iter(lambda:f.read(1024*1024),b''):h.update(data)
if h.hexdigest()!=a.sha256.lower():raise SystemExit('SHA-256 mismatch; nothing installed')
if a.destination.is_symlink():raise SystemExit('Destination symlink refused')
# The archive contains public executable/geo/license assets, not credentials.
# Explicit umask prevents a root installer umask=077 from making core parents
# untraversable by the non-root runtime. Existing other paths are not chmodded.
os.umask(0o022)
a.destination.mkdir(parents=True,exist_ok=True,mode=0o755)
os.chmod(a.destination,0o755)
with zipfile.ZipFile(a.archive) as z:
    files=[name for name in ['xray','geoip.dat','geosite.dat','LICENSE','README.md'] if name in z.namelist()]
    if 'xray' not in files:raise SystemExit('Archive has no top-level Linux xray executable')
    for name in files:
        if (a.destination/name).exists():raise SystemExit('Existing destination files will not be overwritten; stop the service and choose a new version directory')
        info=z.getinfo(name)
        if info.file_size>256*1024*1024 or (info.external_attr>>16)&0o170000==0o120000:raise SystemExit('Unsafe archive member')
    for name in files:
        fd=os.open(a.destination/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o755 if name=='xray' else 0o644)
        with os.fdopen(fd,'wb') as f:f.write(z.read(name))
print('Verified core archive installed at',a.destination.resolve())
print('Update xray_binary/xray_assets in config.json. No other panel is used.')
