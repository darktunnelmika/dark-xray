#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPO="https://github.com/darktunnelmika/dark-xray.git"
BRANCH="main"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "[DARK XRAY] Run as root: sudo bash install-online.sh" >&2
  exit 1
fi

if [[ ! -d /run/systemd/system ]]; then
  echo "[DARK XRAY] A Linux systemd VPS is required." >&2
  exit 1
fi

if [[ -e /opt/dark-xray || -e /etc/dark-xray || -e /var/lib/dark-xray || -e /usr/local/bin/darkxray ]]; then
  echo "[DARK XRAY] Existing installation detected. Fresh installer will not overwrite it." >&2
  echo "Use the DARK XRAY update/maintenance flow instead." >&2
  exit 1
fi

if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y git curl ca-certificates python3 python3-venv
else
  echo "[DARK XRAY] This bootstrap currently supports Debian/Ubuntu (apt)." >&2
  exit 1
fi

TMP="$(mktemp -d /tmp/dark-xray-install.XXXXXX)"
cleanup(){ rm -rf "$TMP"; }
trap cleanup EXIT

git clone --depth 1 --branch "$BRANCH" "$REPO" "$TMP/src"
cd "$TMP/src"

DEFAULT_IP=""
if command -v ip >/dev/null 2>&1; then
  DEFAULT_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' || true)"
fi
DEFAULT_SSH="22"
if command -v sshd >/dev/null 2>&1; then
  DEFAULT_SSH="$(sshd -T 2>/dev/null | awk '/^port /{print $2; exit}' || true)"
  DEFAULT_SSH="${DEFAULT_SSH:-22}"
fi

printf '\n╔══════════════════════════════════════════════════════╗\n'
printf '║                 DARK XRAY INSTALLER                 ║\n'
printf '║              STANDALONE • NO SANAYI                 ║\n'
printf '╚══════════════════════════════════════════════════════╝\n\n'

read -r -p "Public server IP or DNS${DEFAULT_IP:+ [$DEFAULT_IP]}: " PUBLIC_ADDRESS
PUBLIC_ADDRESS="${PUBLIC_ADDRESS:-$DEFAULT_IP}"
if [[ -z "$PUBLIC_ADDRESS" ]]; then
  echo "Public address is required." >&2
  exit 1
fi

read -r -p "SSH port [$DEFAULT_SSH]: " SSH_PORT
SSH_PORT="${SSH_PORT:-$DEFAULT_SSH}"

read -r -p "Owner username [dark]: " OWNER_USER
OWNER_USER="${OWNER_USER:-dark}"

echo
printf '[DARK XRAY] Installing from %s (%s)\n' "$REPO" "$BRANCH"
printf '[DARK XRAY] Public address: %s | SSH port: %s | Owner: %s\n\n' "$PUBLIC_ADDRESS" "$SSH_PORT" "$OWNER_USER"

bash setup.sh \
  --public-address "$PUBLIC_ADDRESS" \
  --ssh-port "$SSH_PORT" \
  --username "$OWNER_USER" \
  --install-os-packages

printf '\n[DARK XRAY] Installation completed.\n'
printf 'Run: darkxray\n'
printf 'Default panel listener remains loopback-only until TLS/domain setup.\n'
