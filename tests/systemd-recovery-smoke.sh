#!/usr/bin/env bash
# Ephemeral real-systemd recovery smoke for GitHub's Ubuntu runner.
# Uses only standard DARK production paths on the disposable runner and removes them on exit.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
REPORT="$ROOT/qa/systemd-recovery.json"
PASS=0
FIRST_PID=0
SECOND_PID=0
THIRD_PID=0

cleanup(){
  systemctl disable --now dark-xray.service >/dev/null 2>&1 || true
  systemctl disable --now dark-xray-guard.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/dark-xray.service /etc/systemd/system/dark-xray-guard.service /usr/local/bin/darkxray
  systemctl daemon-reload >/dev/null 2>&1 || true
  rm -rf /opt/dark-xray /etc/dark-xray /var/lib/dark-xray /usr/local/lib/dark-xray
  if getent passwd darkxray >/dev/null 2>&1; then userdel darkxray >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT

[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo 'root required' >&2; exit 1; }
[[ -d /run/systemd/system ]] || { echo 'real systemd host required' >&2; exit 1; }
for p in /opt/dark-xray /etc/dark-xray /var/lib/dark-xray /usr/local/lib/dark-xray; do
  [[ ! -e "$p" ]] || { echo "refusing pre-existing $p" >&2; exit 1; }
done

mkdir -p "$ROOT/qa"
useradd --system --home-dir /var/lib/dark-xray --shell /usr/sbin/nologin darkxray
UID_DARK="$(id -u darkxray)"; GID_DARK="$(id -g darkxray)"

mkdir -p /opt/dark-xray
for name in backend web tools deploy; do cp -a "$ROOT/$name" "/opt/dark-xray/$name"; done
for name in darkxray requirements.txt LICENSE THIRD-PARTY-NOTICES.md VERSION; do cp -a "$ROOT/$name" "/opt/dark-xray/$name"; done
python3 -m venv /opt/dark-xray/.venv
/opt/dark-xray/.venv/bin/python -m pip install -q --disable-pip-version-check -r /opt/dark-xray/requirements.txt
python3 "$ROOT/tools/fetch-core.py" --version v26.3.27 --destination /usr/local/lib/dark-xray/v26.3.27 >/tmp/dark-core-fetch.json

find /opt/dark-xray -type d -exec chmod 0755 {} +
find /opt/dark-xray -type f -exec chmod 0644 {} +
chmod 0755 /opt/dark-xray/darkxray /opt/dark-xray/.venv/bin/python /opt/dark-xray/.venv/bin/pip
find /opt/dark-xray/.venv/bin -type f -exec chmod 0755 {} +
chown -R root:root /opt/dark-xray

mkdir -p /etc/dark-xray /var/lib/dark-xray
chown root:"$GID_DARK" /etc/dark-xray; chmod 0750 /etc/dark-xray
chown "$UID_DARK":"$GID_DARK" /var/lib/dark-xray; chmod 0700 /var/lib/dark-xray
cat >/etc/dark-xray/config.json <<'JSON'
{
  "public_origin": "http://127.0.0.1:2087",
  "panel_path": "/",
  "public_address": "127.0.0.1",
  "xray_binary": "/usr/local/lib/dark-xray/v26.3.27/xray",
  "xray_assets": "/usr/local/lib/dark-xray/v26.3.27",
  "xray_api_port": 10085,
  "writes_enabled": true,
  "secure_cookie": false,
  "poll_seconds": 1,
  "core_autostart": true,
  "direct_source_verified": false,
  "ip_window_seconds": 120,
  "protected_ports": [22, 2087, 10085],
  "test_engine": false,
  "bind_host": "127.0.0.1",
  "bind_port": 2087,
  "tls_certificate": "",
  "tls_private_key": "",
  "guard_socket": "/run/dark-xray-guard/control.sock",
  "ip_ban_seconds": 1800,
  "ip_exempt_ips": []
}
JSON
chown root:"$GID_DARK" /etc/dark-xray/config.json; chmod 0640 /etc/dark-xray/config.json

printf 'Temporary-CI-Owner-Password-082\nTemporary-CI-Owner-Password-082\n' | \
  runuser -u darkxray -- env DARK_CONFIG=/etc/dark-xray/config.json DARK_DATA=/var/lib/dark-xray \
  /opt/dark-xray/.venv/bin/python /opt/dark-xray/backend/server.py \
  --config /etc/dark-xray/config.json --data /var/lib/dark-xray init --username ci-owner --password-stdin >/tmp/dark-init.log

cp /opt/dark-xray/deploy/dark-xray.service /etc/systemd/system/dark-xray.service
cp /opt/dark-xray/deploy/dark-xray-guard.service /etc/systemd/system/dark-xray-guard.service
chmod 0644 /etc/systemd/system/dark-xray*.service
systemctl daemon-reload
systemctl enable --now dark-xray.service

healthy(){
  systemctl is-active --quiet dark-xray.service || return 1
  runuser -u darkxray -- /opt/dark-xray/.venv/bin/python /opt/dark-xray/tools/doctor.py \
    --config /etc/dark-xray/config.json --data /var/lib/dark-xray >/tmp/dark-doctor.json || return 1
  python3 - <<'PY'
import json
x=json.load(open('/tmp/dark-doctor.json'))
c=x.get('checks',{});r=c.get('panel_route',{})
raise SystemExit(0 if c.get('configuration')=='ok' and c.get('database')=='ok' and r.get('ok') is True else 1)
PY
}

for _ in {1..40}; do healthy && break; sleep .25; done
healthy
FIRST_PID="$(systemctl show -p MainPID --value dark-xray.service)"
[[ "$FIRST_PID" =~ ^[1-9][0-9]*$ ]]
[[ "$(ps -o user= -p "$FIRST_PID" | xargs)" == darkxray ]]
systemctl is-enabled --quiet dark-xray.service
python3 /opt/dark-xray/tools/vps-verify.py --config /etc/dark-xray/config.json --data /var/lib/dark-xray --json-only >/tmp/dark-vps-verify.json
python3 - <<'PY'
import json
x=json.load(open('/tmp/dark-vps-verify.json'));assert x['ready'] is True,(x['failures'],x['warnings'])
PY

# Kill only the main panel process: systemd must finish cgroup cleanup and restart it.
systemctl kill --kill-who=main -s SIGKILL dark-xray.service
for _ in {1..80}; do
  SECOND_PID="$(systemctl show -p MainPID --value dark-xray.service 2>/dev/null || echo 0)"
  if [[ "$SECOND_PID" =~ ^[1-9][0-9]*$ && "$SECOND_PID" != "$FIRST_PID" ]] && healthy; then break; fi
  sleep .25
done
[[ "$SECOND_PID" =~ ^[1-9][0-9]*$ && "$SECOND_PID" != "$FIRST_PID" ]]
healthy

# Normal stop/start models the service boundary used across boot; enablement must persist.
systemctl stop dark-xray.service
[[ "$(systemctl is-active dark-xray.service 2>/dev/null || true)" == inactive ]]
systemctl is-enabled --quiet dark-xray.service
systemctl start dark-xray.service
for _ in {1..40}; do healthy && break; sleep .25; done
healthy
THIRD_PID="$(systemctl show -p MainPID --value dark-xray.service)"
[[ "$THIRD_PID" =~ ^[1-9][0-9]*$ && "$THIRD_PID" != "$SECOND_PID" ]]

# No duplicate owned Xray process should survive crash/stop transitions.
XRAY_COUNT="$(pgrep -u darkxray -f '/usr/local/lib/dark-xray/v26.3.27/xray' | wc -l | xargs)"
[[ "$XRAY_COUNT" == 1 ]]
PASS=1

python3 - "$REPORT" "$FIRST_PID" "$SECOND_PID" "$THIRD_PID" "$XRAY_COUNT" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);data={
 'real_systemd_tested':True,
 'service_user_verified':'darkxray',
 'autostart_enabled':True,
 'sigkill_restart_verified':True,
 'stop_start_recovery_verified':True,
 'vps_verify_ready_after_install':True,
 'single_owned_xray_after_recovery':int(sys.argv[5])==1,
 'pids':{'initial':int(sys.argv[2]),'after_sigkill':int(sys.argv[3]),'after_stop_start':int(sys.argv[4])},
 'passed':True,
 'real_machine_reboot_tested':False
}
p.write_text(json.dumps(data,indent=2),encoding='utf-8')
PY
chmod 0644 "$REPORT"
cat "$REPORT"
