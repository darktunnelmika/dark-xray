#!/usr/bin/env python3
from pathlib import Path
p=Path('tests/systemd-recovery-smoke.sh')
s=p.read_text(encoding='utf-8')
old1="""/opt/dark-xray/.venv/bin/python -m pip install -q --disable-pip-version-check -r /opt/dark-xray/requirements.txt
python3 \"$ROOT/tools/fetch-core.py\" --version v26.3.27 --destination /usr/local/lib/dark-xray/v26.3.27 >/tmp/dark-core-fetch.json
"""
new1="""/opt/dark-xray/.venv/bin/python -m pip install -q --disable-pip-version-check -r /opt/dark-xray/requirements.txt
/opt/dark-xray/.venv/bin/python -m pip check
/opt/dark-xray/.venv/bin/python -c 'import cryptography,fastapi,psutil,pydantic,uvicorn; print(\"runtime dependency import check: ok\")'
python3 \"$ROOT/tools/fetch-core.py\" --version v26.3.27 --destination /usr/local/lib/dark-xray/v26.3.27 >/tmp/dark-core-fetch.json
"""
old2="""/opt/dark-xray/.venv/bin/python /opt/dark-xray/tools/vps-verify.py \\
  --config /etc/dark-xray/config.json --data /var/lib/dark-xray --json-only >/tmp/dark-vps-verify.json
"""
new2="""DARK_CONFIG=/etc/dark-xray/config.json DARK_DATA=/var/lib/dark-xray \\
PYTHONHOME=/invalid-dark-ci PYTHONPATH=/invalid-dark-ci \\
  /opt/dark-xray/darkxray vps-verify --json-only >/tmp/dark-vps-verify.json
"""
if old1 not in s: raise SystemExit('dependency anchor missing')
if old2 not in s: raise SystemExit('vps verify anchor missing')
s=s.replace(old1,new1,1).replace(old2,new2,1)
p.write_text(s,encoding='utf-8')
