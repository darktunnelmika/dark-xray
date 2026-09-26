#!/usr/bin/env bash
# DARK XRAY lightweight Node Agent installer — no panel UI, owner, finance, or reseller runtime.
set -Eeuo pipefail
umask 077
REPO="https://github.com/darktunnelmika/dark-xray.git"
REF="${DARK_XRAY_REF:-main}"
CORE_VERSION="${DARK_XRAY_CORE_VERSION:-v26.3.27}"
NODE_PORT="${DARK_NODE_PORT:-8443}"

fail(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
ask(){ local q="$1" d="${2:-}" v; read -r -p "$q${d:+ [$d]}: " v; printf '%s' "${v:-$d}"; }
yesno(){ local q="$1" v; read -r -p "$q [y/N]: " v; [[ "${v,,}" == y || "${v,,}" == yes ]]; }
version_ge(){ printf '%s\n%s\n' "$2" "$1" | sort -V -C; }
open_local_firewall(){
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
    ufw allow 80/tcp >/dev/null
    ufw allow "$NODE_PORT/tcp" >/dev/null
    ufw reload >/dev/null
    printf 'UFW: opened TCP 80 and %s.\n' "$NODE_PORT"
  fi
  if systemctl is-active --quiet firewalld 2>/dev/null; then
    firewall-cmd --permanent --add-service=http >/dev/null
    firewall-cmd --permanent --add-port="$NODE_PORT/tcp" >/dev/null
    firewall-cmd --reload >/dev/null
    printf 'firewalld: opened TCP 80 and %s.\n' "$NODE_PORT"
  fi
}
[[ ${EUID:-$(id -u)} -eq 0 ]] || fail "Run as root"
command -v apt-get >/dev/null || fail "Debian/Ubuntu apt is required"
[[ -d /run/systemd/system ]] || fail "systemd is required"
[[ ! -e /opt/dark-xray && ! -e /etc/dark-xray ]] || fail "Full DARK XRAY panel detected; Node Agent requires a separate VPS"
[[ ! -e /opt/dark-xray-node && ! -e /etc/dark-xray-node ]] || fail "DARK Node Agent is already installed"

printf '\nDARK XRAY · LIGHTWEIGHT NODE AGENT\n'
printf 'No Web UI / Owner / Finance / Reseller database will be installed.\n\n'
DOMAIN="$(ask 'Node HTTPS domain (DNS must point to this VPS)')"
EMAIL="$(ask 'ACME email')"
NAME="$(ask 'Node display name' "$(hostname -s)")"
DATA_DEFAULT="$DOMAIN"
if command -v getent >/dev/null 2>&1; then
  RESOLVED_IP="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1;exit}' || true)"
  [[ -n "$RESOLVED_IP" ]] && DATA_DEFAULT="$RESOLVED_IP"
fi
DATA_ADDRESS="$(ask 'Client-facing node address (public IP recommended; HTTPS control domain stays separate)' "$DATA_DEFAULT")"
NODE_PORT="$(ask 'Node Agent HTTPS port' "$NODE_PORT")"
VERIFY_SOURCE=0
if yesno 'Does Xray on this Node see the real client packet source IP directly? Enable packet-level IP enforcement only if verified'; then VERIFY_SOURCE=1; fi
SSH_PORT="$(sshd -T 2>/dev/null | awk '/^port /{print $2;exit}' || true)"; SSH_PORT="${SSH_PORT:-22}"
[[ "$NODE_PORT" =~ ^[0-9]+$ ]] && ((NODE_PORT>=1024 && NODE_PORT<=65535)) || fail "Invalid node port: use 1024-65535 (recommended: 8443)"
[[ "$NODE_PORT" != "$SSH_PORT" && "$NODE_PORT" != "10085" ]] || fail "Node port conflicts with SSH/Xray API"
[[ "$DOMAIN" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]] || fail "Invalid domain"
[[ "$EMAIL" == *@*.* ]] || fail "Invalid email"

printf '\nInstalling prerequisites...\n'
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -q git curl python3 python3-venv ca-certificates certbot nftables iproute2 >/dev/null

PYTHON_BIN=python3
PYVER="$($PYTHON_BIN -c 'import sys;print(".".join(map(str,sys.version_info[:3])))')"
if ! version_ge "$PYVER" "3.10"; then
  printf 'System Python %s is too old; trying to install Python 3.10...\n' "$PYVER"
  if apt-get install -y -q python3.10 python3.10-venv >/dev/null 2>&1; then
    PYTHON_BIN=python3.10
    PYVER="$($PYTHON_BIN -c 'import sys;print(".".join(map(str,sys.version_info[:3])))')"
  else
    fail "Python 3.10+ is required. Use Ubuntu 22.04+/24.04 or Debian 12+, or install Python 3.10+ first."
  fi
fi
printf 'Python: %s (%s)\n' "$PYTHON_BIN" "$PYVER"

RESOLVED_IP="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1;exit}' || true)"
[[ -n "$RESOLVED_IP" ]] || fail "DNS for $DOMAIN does not resolve to an IPv4 address yet"
printf 'DNS: %s -> %s\n' "$DOMAIN" "$RESOLVED_IP"

if ss -lntp '( sport = :80 )' 2>/dev/null | tail -n +2 | grep -q .; then
  ss -lntp '( sport = :80 )' || true
  fail "TCP port 80 is already in use; Certbot standalone needs it temporarily"
fi
open_local_firewall

TMP="$(mktemp -d /tmp/dark-xray-node.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
git init -q "$TMP/src"
git -C "$TMP/src" remote add origin "$REPO"
git -C "$TMP/src" fetch -q --depth 1 origin "$REF" || fail "Cannot fetch DARK XRAY source ref: $REF"
git -C "$TMP/src" checkout -q --detach FETCH_HEAD
SHA="$(git -C "$TMP/src" rev-parse HEAD)"
printf 'Source commit: %s\n' "$SHA"

CERT_NAME="dark-xray-node-$DOMAIN"
printf '\nIssuing Node Agent TLS certificate for %s...\n' "$DOMAIN"
if ! certbot certonly --standalone --non-interactive --agree-tos --preferred-challenges http \
  --email "$EMAIL" --cert-name "$CERT_NAME" -d "$DOMAIN"; then
  printf '\nDARK XRAY TLS PREFLIGHT FAILED\n' >&2
  printf 'DNS resolves %s -> %s and local TCP/80 is free.\n' "$DOMAIN" "$RESOLVED_IP" >&2
  printf 'If Certbot reports connection refused/timeout, open inbound TCP/80 in the VPS provider firewall/security group.\n' >&2
  printf 'Also keep TCP/%s open for the Node Agent. Then rerun this installer.\n' "$NODE_PORT" >&2
  exit 1
fi
LIVE="/etc/letsencrypt/live/$CERT_NAME"
[[ -f "$LIVE/fullchain.pem" && -f "$LIVE/privkey.pem" ]] || fail "Certbot did not produce the expected certificate"

PROVISION_ARGS=(--domain "$DOMAIN" --port "$NODE_PORT" --data-address "$DATA_ADDRESS" --name "$NAME"
  --ssh-port "$SSH_PORT" --core-version "$CORE_VERSION" --cert "$LIVE/fullchain.pem" --key "$LIVE/privkey.pem"
  --source-commit "$SHA" --source-ref "$REF")
[[ "$VERIFY_SOURCE" == 1 ]] && PROVISION_ARGS+=(--verified-direct-sources)
"$PYTHON_BIN" "$TMP/src/tools/provision_node.py" "${PROVISION_ARGS[@]}"

HOOK="/etc/letsencrypt/renewal-hooks/deploy/dark-xray-node-$DOMAIN"
cat >"$HOOK" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
install -o root -g darkxray -m 0640 "$LIVE/fullchain.pem" /etc/dark-xray-node/tls/cert.pem
install -o root -g darkxray -m 0640 "$LIVE/privkey.pem" /etc/dark-xray-node/tls/key.pem
systemctl try-restart dark-xray-node.service
EOF
chmod 0750 "$HOOK"
systemctl enable --now certbot.timer >/dev/null 2>&1 || true

printf '\nNode Agent installed.\n'
printf 'Service: dark-xray-node.service\n'
printf 'Origin : https://%s:%s\n' "$DOMAIN" "$NODE_PORT"
PAIR_CODE="$("$PYTHON_BIN" - <<'PY'
import json
print(json.load(open('/var/lib/dark-xray-node/pair.json'))['pairCode'])
PY
)"
printf '\n============================================================\n'
printf 'DARK NODE PAIR CODE — paste once into Hub -> Nodes -> Add Node\n'
printf '============================================================\n'
printf '%s\n' "$PAIR_CODE"
printf '============================================================\n'
if [[ "$VERIFY_SOURCE" == 1 ]]; then
  printf 'IP Guard: direct-source packet enforcement approved; Hub controls only Xray data ports.\n'
else
  printf 'IP Guard: observe-only until direct source IP is explicitly verified on this VPS.\n'
fi
printf '\nLocal commands: darknode status | darknode logs | darknode pair-info\n'
