#!/usr/bin/env bash
# ==============================================================================
#  DARK VPN · GRE DIRECT - installer
#  curl -fsSL https://raw.githubusercontent.com/darktunnelmika/dark-xray/feature/dark-gre-direct-v1/standalone/dark-gre/install.sh | bash
# ==============================================================================
set -u

RAW="https://raw.githubusercontent.com/darktunnelmika/dark-xray/feature/dark-gre-direct-v1/standalone/dark-gre/dark-gre.sh"
DEST="/usr/local/bin/darkgre"
BASE_DIR="/etc/dark-gre"

R=$'\e[38;5;203m'; G=$'\e[38;5;114m'; C=$'\e[38;5;81m'; D=$'\e[38;5;244m'; N=$'\e[0m'
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
bad()  { printf '  %s✗%s %s\n' "$R" "$N" "$1" >&2; }
info() { printf '  %s·%s %s\n' "$C" "$N" "$1"; }

echo
printf '  %sDARK VPN · GRE DIRECT%s  installer\n' "$C" "$N"
echo

if [ "$(id -u)" -ne 0 ]; then
  bad "must run as root - try again with sudo"
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  info "installing: curl"
  if   command -v apt-get >/dev/null 2>&1; then apt-get update -qq && apt-get install -y -qq curl
  elif command -v dnf     >/dev/null 2>&1; then dnf install -y -q curl
  elif command -v yum     >/dev/null 2>&1; then yum install -y -q curl
  elif command -v apk     >/dev/null 2>&1; then apk add --no-cache curl
  else bad "install curl manually"; exit 1; fi
fi

tmp="$(mktemp)" || { bad "cannot create temp file"; exit 1; }
trap 'rm -f "$tmp"' EXIT

info "downloading manager"
if ! curl -fsSL --retry 3 --max-time 60 -o "$tmp" "$RAW"; then
  bad "download failed - check connectivity to raw.githubusercontent.com"
  exit 1
fi

sed -i 's/\r$//' "$tmp"
grep -q 'DARKVPN-GRE-SCRIPT' "$tmp" || { bad "downloaded file is not DARK GRE"; exit 1; }
bash -n "$tmp" 2>/dev/null || { bad "downloaded file has syntax errors"; exit 1; }

ver="$(grep -m1 '^SCRIPT_VER=' "$tmp" | cut -d'"' -f2)"
mkdir -p "$BASE_DIR" && chmod 700 "$BASE_DIR"
[ -f "$DEST" ] && cp -f "$DEST" "$DEST.bak" 2>/dev/null
install -m 0755 "$tmp" "$DEST" || { bad "could not write $DEST"; exit 1; }
echo "$RAW" > "$BASE_DIR/update.url"
chmod 600 "$BASE_DIR/update.url"

ok "installed v${ver:-?} to $DEST"
echo
printf '  %srun it with:%s  darkgre\n' "$D" "$N"
echo

if [ -r /dev/tty ]; then
  exec "$DEST" </dev/tty
fi
