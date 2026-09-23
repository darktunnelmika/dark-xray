#!/usr/bin/env bash
# Disposable real Certbot + real systemd acceptance for DARK panel TLS.
# ACME uses a local Pebble TEST CA with validation disabled; it is not public
# Let's Encrypt/DNS/WAN acceptance. No production/customer state is touched.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
REPORT="$ROOT/qa/panel-tls-acme-systemd.json"
PHASE=bootstrap
PEBBLE_PID=""
PEBBLE_VERSION=v2.10.1
PEBBLE_COMMIT=b1e1ca4f3c30abb64111adaca4544bc5374cc306
PEBBLE_ARCHIVE_SHA256=4f2fcb5bca8c85c9cf73ad140fccfc0d2be40bd81ab99879c79b7b8a0b4f70ed
DOMAIN=panel.dark.test
PANEL_PORT=2087
PANEL_PATH=/ci
CERTNAME="dark-xray-$DOMAIN"
PASSWORD="${DARK_TLS_CI_PASSWORD:-}"
PEBBLE_SRC=/tmp/dark-pebble-src
PEBBLE_BIN_DIR=/tmp/dark-pebble-bin
REQUESTS_BUNDLE=/tmp/dark-pebble-requests-ca.pem
ISSUANCE_ROOT=/usr/local/share/ca-certificates/dark-ci-pebble-root.crt
INITIAL_FP=""
RENEWED_FP=""
INITIAL_PID=0
RENEWED_PID=0

cleanup(){
  set +e
  if [[ -n "$PEBBLE_PID" ]]; then kill "$PEBBLE_PID" >/dev/null 2>&1 || true; wait "$PEBBLE_PID" >/dev/null 2>&1 || true; fi
  systemctl disable --now dark-xray.service dark-xray-update.service dark-xray-guard.service >/dev/null 2>&1 || true
  systemctl disable --now certbot.timer >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/dark-xray.service /etc/systemd/system/dark-xray-update.service /etc/systemd/system/dark-xray-guard.service
  systemctl daemon-reload >/dev/null 2>&1 || true
  rm -rf /opt/dark-xray /etc/dark-xray /var/lib/dark-xray /usr/local/lib/dark-xray
  rm -f /usr/local/bin/darkxray
  rm -rf /etc/letsencrypt /var/lib/letsencrypt /var/log/letsencrypt
  rm -f "$ISSUANCE_ROOT" "$REQUESTS_BUNDLE"
  update-ca-certificates >/dev/null 2>&1 || true
  if getent passwd darkxray >/dev/null 2>&1; then userdel darkxray >/dev/null 2>&1 || true; fi
  rm -rf "$PEBBLE_SRC" "$PEBBLE_BIN_DIR" /tmp/dark-pebble.tar.gz /tmp/dark-provision.exp /tmp/dark-login-headers /tmp/dark-login-body /tmp/dark-health-headers /tmp/dark-health-body
}
finish(){
  local rc=$?
  trap - EXIT
  mkdir -p "$ROOT/qa"
  if (( rc != 0 )) && [[ ! -f "$REPORT" ]]; then
    python3 - "$REPORT" "$rc" "$PHASE" <<'PY'
import json,sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
  "passed": False,
  "exit_code": int(sys.argv[2]),
  "failure_phase": sys.argv[3],
  "real_certbot_tested": True,
  "real_systemd_tested": True,
  "production_ca_tested": False,
  "public_dns_tested": False,
  "external_http01_validation_tested": False,
},indent=2)+"\n",encoding="utf-8")
PY
    chmod 0644 "$REPORT" || true
  fi
  cleanup
  exit "$rc"
}
trap finish EXIT

[[ ${EUID:-$(id -u)} -eq 0 ]] || { echo "root required" >&2; exit 1; }
[[ -d /run/systemd/system ]] || { echo "real systemd host required" >&2; exit 1; }
[[ -n "$PASSWORD" ]] || { echo "DARK_TLS_CI_PASSWORD is required" >&2; exit 1; }
for p in /opt/dark-xray /etc/dark-xray /var/lib/dark-xray /usr/local/lib/dark-xray; do
  [[ ! -e "$p" ]] || { echo "refusing pre-existing $p" >&2; exit 1; }
done
mkdir -p "$ROOT/qa"

PHASE=host-prerequisites
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -q certbot expect curl ca-certificates python3-venv openssl git >/dev/null
systemctl cat certbot.timer >/dev/null

PHASE=provision-dark
cat >/tmp/dark-provision.exp <<'EOF'
set timeout 1200
set pass $env(DARK_TLS_CI_PASSWORD)
spawn bash ./setup.sh --public-address 127.0.0.1 --ssh-port 22 --username tls-ci --port 2087 --panel-path /ci --core-version v26.3.27
expect {
  -re {DARK owner password \([0-9]+-[0-9]+ characters\): $} { send "$pass\r"; exp_continue }
  -re {Repeat owner password: $} { send "$pass\r"; exp_continue }
  timeout { exit 124 }
  eof
}
catch wait result
exit [lindex $result 3]
EOF
cd "$ROOT"
expect /tmp/dark-provision.exp >/tmp/dark-provision.log
systemctl is-enabled --quiet dark-xray.service
systemctl is-active --quiet dark-xray.service
python3 - <<'PY'
import json
from pathlib import Path
c=json.loads(Path('/etc/dark-xray/config.json').read_text())
assert c['public_origin']=='http://127.0.0.1:2087'
assert c['bind_host']=='127.0.0.1' and c['bind_port']==2087
assert c['secure_cookie'] is False
PY

PHASE=pebble-download
rm -rf "$PEBBLE_SRC" "$PEBBLE_BIN_DIR"
git init -q "$PEBBLE_SRC"
git -C "$PEBBLE_SRC" remote add origin https://github.com/letsencrypt/pebble.git
git -C "$PEBBLE_SRC" fetch -q --depth=1 origin "$PEBBLE_COMMIT"
git -C "$PEBBLE_SRC" checkout -q --detach FETCH_HEAD
[[ "$(git -C "$PEBBLE_SRC" rev-parse HEAD)" == "$PEBBLE_COMMIT" ]]
curl -fL --retry 3 --retry-delay 1 \
  "https://github.com/letsencrypt/pebble/releases/download/$PEBBLE_VERSION/pebble-linux-amd64.tar.gz" \
  -o /tmp/dark-pebble.tar.gz
echo "$PEBBLE_ARCHIVE_SHA256  /tmp/dark-pebble.tar.gz" | sha256sum -c -
mkdir -p "$PEBBLE_BIN_DIR"
tar -xzf /tmp/dark-pebble.tar.gz -C "$PEBBLE_BIN_DIR"
# GitHub Actions artifacts do not preserve executable mode; the release archive
# is built from those artifacts. Locate the checksum-pinned binary by exact name,
# then restore only its expected executable mode on this disposable runner.
PEBBLE_BIN="$(find "$PEBBLE_BIN_DIR" -type f -name pebble -print -quit)"
[[ -n "$PEBBLE_BIN" && -f "$PEBBLE_BIN" ]]
chmod 0755 "$PEBBLE_BIN"
[[ -x "$PEBBLE_BIN" ]]

PHASE=pebble-start
cd "$PEBBLE_SRC"
env PEBBLE_VA_ALWAYS_VALID=1 PEBBLE_VA_NOSLEEP=1 PEBBLE_WFE_NONCEREJECT=0 \
  "$PEBBLE_BIN" -config test/config/pebble-config.json >/tmp/dark-pebble.log 2>&1 &
PEBBLE_PID=$!
cd "$ROOT"
for _ in {1..80}; do
  if curl -fsS --cacert "$PEBBLE_SRC/test/certs/pebble.minica.pem" https://127.0.0.1:14000/dir >/dev/null; then break; fi
  sleep .1
done
curl -fsS --cacert "$PEBBLE_SRC/test/certs/pebble.minica.pem" https://127.0.0.1:14000/dir >/dev/null

PHASE=trust-test-ca
curl -fsS --cacert "$PEBBLE_SRC/test/certs/pebble.minica.pem" \
  https://127.0.0.1:15000/roots/0 -o "$ISSUANCE_ROOT"
openssl x509 -in "$ISSUANCE_ROOT" -noout -subject >/dev/null
update-ca-certificates >/dev/null
cat /etc/ssl/certs/ca-certificates.crt "$PEBBLE_SRC/test/certs/pebble.minica.pem" > "$REQUESTS_BUNDLE"
mkdir -p /etc/letsencrypt
cat >/etc/letsencrypt/cli.ini <<'EOF'
server = https://127.0.0.1:14000/dir
no-eff-email = true
EOF

PHASE=initial-issuance
export REQUESTS_CA_BUNDLE="$REQUESTS_BUNDLE"
darkxray domain --domain "$DOMAIN" --email tls-ci@example.test --port "$PANEL_PORT" --agree-tos >/tmp/dark-domain-issue.log
systemctl is-enabled --quiet certbot.timer
systemctl is-active --quiet certbot.timer
systemctl is-active --quiet dark-xray.service
INITIAL_PID="$(systemctl show -p MainPID --value dark-xray.service)"
[[ "$INITIAL_PID" =~ ^[1-9][0-9]*$ ]]
LINEAGE="/etc/letsencrypt/live/$CERTNAME"
[[ -s "$LINEAGE/fullchain.pem" && -s "$LINEAGE/privkey.pem" ]]
[[ -s /etc/dark-xray/tls-source.json && -x /etc/letsencrypt/renewal-hooks/deploy/dark-xray-panel ]]
[[ "$(stat -c '%a' /etc/dark-xray/tls-source.json)" == 600 ]]
[[ "$(stat -c '%a' /etc/letsencrypt/renewal-hooks/deploy/dark-xray-panel)" == 750 ]]
INITIAL_FP="$(openssl x509 -in /etc/dark-xray/tls/cert.pem -noout -fingerprint -sha256 | cut -d= -f2)"
python3 - "$DOMAIN" "$PANEL_PORT" "$LINEAGE" <<'PY'
import json,sys
from pathlib import Path
domain,port,lineage=sys.argv[1],int(sys.argv[2]),sys.argv[3]
c=json.loads(Path('/etc/dark-xray/config.json').read_text())
s=json.loads(Path('/etc/dark-xray/tls-source.json').read_text())
assert c['public_origin']==f'https://{domain}:{port}'
assert c['bind_port']==port and c['secure_cookie'] is True
assert c['tls_certificate']=='/etc/dark-xray/tls/cert.pem'
assert c['tls_private_key']=='/etc/dark-xray/tls/key.pem'
assert s=={'lineage':lineage,'domain':domain}
PY

PHASE=https-health-and-cookie
curl -fsS --resolve "$DOMAIN:$PANEL_PORT:127.0.0.1" --cacert "$ISSUANCE_ROOT" \
  -D /tmp/dark-health-headers -o /tmp/dark-health-body "https://$DOMAIN:$PANEL_PORT/health"
python3 - <<'PY'
import json
from pathlib import Path
h=Path('/tmp/dark-health-headers').read_text().lower()
b=json.loads(Path('/tmp/dark-health-body').read_text())
assert b.get('service')=='DARK XRAY' and b.get('mode')=='standalone'
assert 'strict-transport-security:' in h and 'max-age=' in h
PY
LOGIN_JSON="$(python3 - <<'PY'
import json,os
print(json.dumps({'username':'tls-ci','password':os.environ['DARK_TLS_CI_PASSWORD'],'otp':''}))
PY
)"
curl -fsS --resolve "$DOMAIN:$PANEL_PORT:127.0.0.1" --cacert "$ISSUANCE_ROOT" \
  -D /tmp/dark-login-headers -o /tmp/dark-login-body \
  -H 'Content-Type: application/json' --data "$LOGIN_JSON" \
  "https://$DOMAIN:$PANEL_PORT$PANEL_PATH/api/auth/login"
python3 - <<'PY'
import json
from pathlib import Path
body=json.loads(Path('/tmp/dark-login-body').read_text())
assert body.get('role')=='owner' and body.get('id')=='tls-ci' and body.get('csrf')
headers=Path('/tmp/dark-login-headers').read_text()
cookies=[x for x in headers.splitlines() if x.lower().startswith('set-cookie:')]
assert len(cookies)==1
cookie=cookies[0].lower()
assert '; secure' in cookie and '; httponly' in cookie and 'samesite=strict' in cookie and 'path=/ci' in cookie
PY
rm -f /tmp/dark-login-headers /tmp/dark-login-body

PHASE=real-renewal
certbot renew --cert-name "$CERTNAME" --force-renewal --no-random-sleep-on-renew >/tmp/dark-certbot-renew.log
systemctl is-active --quiet dark-xray.service
RENEWED_PID="$(systemctl show -p MainPID --value dark-xray.service)"
[[ "$RENEWED_PID" =~ ^[1-9][0-9]*$ && "$RENEWED_PID" != "$INITIAL_PID" ]]
RENEWED_FP="$(openssl x509 -in /etc/dark-xray/tls/cert.pem -noout -fingerprint -sha256 | cut -d= -f2)"
[[ -n "$INITIAL_FP" && -n "$RENEWED_FP" && "$RENEWED_FP" != "$INITIAL_FP" ]]
cmp -s /etc/dark-xray/tls/cert.pem "$LINEAGE/fullchain.pem"
cmp -s /etc/dark-xray/tls/key.pem "$LINEAGE/privkey.pem"
curl -fsS --resolve "$DOMAIN:$PANEL_PORT:127.0.0.1" --cacert "$ISSUANCE_ROOT" \
  "https://$DOMAIN:$PANEL_PORT/health" | python3 -c 'import json,sys;x=json.load(sys.stdin);assert x["service"]=="DARK XRAY" and x["mode"]=="standalone"'

PHASE=report
python3 - "$REPORT" "$PEBBLE_VERSION" "$PEBBLE_COMMIT" "$INITIAL_PID" "$RENEWED_PID" "$INITIAL_FP" "$RENEWED_FP" <<'PY'
import json,sys
from pathlib import Path
out={
  "passed": True,
  "real_certbot_tested": True,
  "real_systemd_tested": True,
  "certbot_timer_enabled_and_active": True,
  "certificate_issued": True,
  "forced_renewal_completed": True,
  "deploy_hook_observed_via_service_restart": int(sys.argv[5])!=int(sys.argv[4]),
  "panel_https_after_issue": True,
  "panel_https_after_renewal": True,
  "hsts_verified": True,
  "secure_session_cookie_verified": True,
  "certificate_changed_on_renewal": sys.argv[6]!=sys.argv[7],
  "acme_server": f"Pebble {sys.argv[2]} local test CA",
  "pebble_source_commit": sys.argv[3],
  "production_ca_tested": False,
  "public_dns_tested": False,
  "external_http01_validation_tested": False,
  "pebble_validation_mode": "always-valid test mode",
  "real_machine_reboot_tested": False,
  "customer_data_used": False,
}
Path(sys.argv[1]).write_text(json.dumps(out,indent=2)+"\n",encoding="utf-8")
PY
chmod 0644 "$REPORT"
cat "$REPORT"
