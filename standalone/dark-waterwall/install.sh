#!/usr/bin/env bash
# ==============================================================================
#  DARK VPN · WATERWALL DIRECT - installer
# ==============================================================================
set -u

VERSION="v0.1.0-rc1"
DEST="${DARK_WW_DEST:-/usr/local/bin/darkwater}"
BASE_DIR="${DARK_WW_BASE_DIR:-/etc/dark-waterwall}"

R=$'\e[38;5;203m'; G=$'\e[38;5;114m'; C=$'\e[38;5;81m'; D=$'\e[38;5;244m'; N=$'\e[0m'
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
bad()  { printf '  %s✗%s %s\n' "$R" "$N" "$1" >&2; }
info() { printf '  %s·%s %s\n' "$C" "$N" "$1"; }

echo
printf '  %sDARK VPN · WATERWALL DIRECT%s  installer %s\n' "$C" "$N" "$VERSION"
echo

if [ "$(id -u)" -ne 0 ]; then bad "must run as root - try again with sudo"; exit 1; fi

if ! command -v curl >/dev/null 2>&1; then
  info "installing: curl"
  if command -v apt-get >/dev/null 2>&1; then apt-get update -qq && apt-get install -y -qq curl ca-certificates
  elif command -v dnf >/dev/null 2>&1; then dnf install -y -q curl ca-certificates
  elif command -v yum >/dev/null 2>&1; then yum install -y -q curl ca-certificates
  elif command -v apk >/dev/null 2>&1; then apk add --no-cache curl ca-certificates
  else bad "install curl manually"; exit 1; fi
fi

tmp="$(mktemp)" || { bad "cannot create temp file"; exit 1; }
trap 'rm -f "$tmp"' EXIT

download_one(){
  local url="$1"
  info "trying: $url"
  curl -4 -fsSL --retry 2 --retry-all-errors --connect-timeout 8 --max-time 60 -o "$tmp" "$url"
}

if [ -n "${DARK_WW_RAW_URL:-}" ]; then
  URLS=("$DARK_WW_RAW_URL")
else
  URLS=(
    "https://cdn.jsdelivr.net/gh/darktunnelmika/dark-xray@main/standalone/dark-waterwall/dark-waterwall.sh"
    "https://raw.githubusercontent.com/darktunnelmika/dark-xray/main/standalone/dark-waterwall/dark-waterwall.sh"
    "https://raw.githubusercontent.com/darktunnelmika/dark-xray/feature/dark-waterwall-direct-v1/standalone/dark-waterwall/dark-waterwall.sh"
  )
fi

SELECTED_URL=""
for url in "${URLS[@]}"; do
  : >"$tmp"
  if download_one "$url"; then
    sed -i 's/\r$//' "$tmp"
    if grep -q 'DARKVPN-WATERWALL-SCRIPT' "$tmp" && bash -n "$tmp" 2>/dev/null; then SELECTED_URL="$url"; break; fi
  fi
done

if [ -z "$SELECTED_URL" ]; then
  bad "all DARK script mirrors failed"
  bad "check outbound HTTPS/DNS connectivity"
  exit 1
fi

ver="$(grep -m1 '^SCRIPT_VER=' "$tmp" | cut -d'"' -f2)"
mkdir -p "$BASE_DIR" && chmod 700 "$BASE_DIR"
[ -f "$DEST" ] && cp -f "$DEST" "$DEST.bak" 2>/dev/null || true
install -m 0755 "$tmp" "$DEST" || { bad "could not write $DEST"; exit 1; }
printf '%s\n' "$SELECTED_URL" >"$BASE_DIR/update.url"; chmod 600 "$BASE_DIR/update.url"
ok "installed v${ver:-?} to $DEST"
ok "source mirror: $SELECTED_URL"

if [ "${DARK_WW_INSTALL_ONLY:-0}" != 1 ]; then
  info "repairing runtime units"
  DARK_WW_REPAIR_ONLY=1 "$DEST" || { bad "runtime repair failed"; exit 1; }
  ok "runtime units repaired"
fi

echo
printf '  %srun it with:%s  darkwater\n' "$D" "$N"
echo

if [ "${DARK_WW_INSTALL_ONLY:-0}" != 1 ]; then
  if [ -t 0 ]; then exec "$DEST"
  elif { exec 3<>/dev/tty; } 2>/dev/null; then exec "$DEST" <&3 >&3 2>&3
  fi
fi
