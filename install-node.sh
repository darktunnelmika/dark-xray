#!/usr/bin/env bash
# DARK XRAY lightweight Node Agent installer — no panel UI, owner, finance, or reseller runtime.
set -Eeuo pipefail
umask 077
REPO="https://github.com/darktunnelmika/dark-xray.git"
REF="${DARK_XRAY_REF:-main}"
CORE_VERSION="${DARK_XRAY_CORE_VERSION:-v26.3.27}"
NODE_PORT="${DARK_NODE_PORT:-9443}"

fail(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
ask(){ local q="$1" d="${2:-}" v; read -r -p "$q${d:+ [$d]}: " v; printf '%s' "${v:-$d}"; }
yesno(){ local q="$1" v; read -r -p "$q [y/N]: " v; [[ "${v,,}" == y || "${v,,}" == yes ]]; }
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
[[ "$NODE_PORT" =~ ^[0-9]+$ ]] && ((NODE_PORT>=1024 && NODE_PORT<=65535)) || fail "Invalid node port"
[[ "$NODE_PORT" != "$SSH_PORT" && "$NODE_PORT" != "10085" ]] || fail "Node port conflicts with SSH/Xray API"
[[ "$DOMAIN" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]] || fail "Invalid domain"
[[ "$EMAIL" == *@*.* ]] || fail "Invalid email"

printf '\nInstalling prerequisites...\n'
apt-get update -qq
apt-get install -y -q git python3 python3-venv ca-certificates certbot nftables >/dev/null

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
certbot certonly --standalone --non-interactive --agree-tos --preferred-challenges http   --email "$EMAIL" --cert-name "$CERT_NAME" -d "$DOMAIN"
LIVE="/etc/letsencrypt/live/$CERT_NAME"
[[ -f "$LIVE/fullchain.pem" && -f "$LIVE/privkey.pem" ]] || fail "Certbot did not produce the expected certificate"

PROVISION_ARGS=(--domain "$DOMAIN" --port "$NODE_PORT" --data-address "$DATA_ADDRESS" --name "$NAME"
  --ssh-port "$SSH_PORT" --core-version "$CORE_VERSION" --cert "$LIVE/fullchain.pem" --key "$LIVE/privkey.pem"
  --source-commit "$SHA" --source-ref "$REF")
[[ "$VERIFY_SOURCE" == 1 ]] && PROVISION_ARGS+=(--verified-direct-sources)
python3 "$TMP/src/tools/provision_node.py" "${PROVISION_ARGS[@]}"

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
printf '\nPAIR CODE (paste once into DARK XRAY Hub → Nodes → Add Node):\n'
python3 - <<'PY'
import json
print(json.load(open('/var/lib/dark-xray-node/pair.json'))['pairCode'])
PY
if [[ "$VERIFY_SOURCE" == 1 ]]; then
  printf 'IP Guard: direct-source packet enforcement approved; Hub controls only Xray data ports.\n'
else
  printf 'IP Guard: observe-only until direct source IP is explicitly verified on this VPS.\n'
fi
printf '\nLocal commands: darknode status | darknode logs | darknode pair-info\n'
