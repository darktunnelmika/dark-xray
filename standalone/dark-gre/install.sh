#!/usr/bin/env bash
# ==============================================================================
#  DARK VPN · GRE DIRECT - installer
# ==============================================================================
set -u

RAW="${DARK_GRE_RAW_URL:-https://raw.githubusercontent.com/darktunnelmika/dark-xray/feature/dark-gre-direct-v1/standalone/dark-gre/dark-gre.sh}"
DEST="/usr/local/bin/darkgre"
BASE_DIR="/etc/dark-gre"

R=$'\e[38;5;203m'; G=$'\e[38;5;114m'; C=$'\e[38;5;81m'; D=$'\e[38;5;244m'; N=$'\e[0m'
ok()   { printf '  %s+%s %s\n' "$G" "$N" "$1"; }
bad()  { printf '  %sx%s %s\n' "$R" "$N" "$1" >&2; }
info() { printf '  %s>%s %s\n' "$C" "$N" "$1"; }

echo
printf '  %sDARK VPN · GRE DIRECT%s  installer\n\n' "$C" "$N"

[ "$(id -u)" -eq 0 ] || { bad "must run as root"; exit 1; }

for c in curl; do
  command -v "$c" >/dev/null 2>&1 && continue
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl >/dev/null
  else
    bad "curl is required"; exit 1
  fi
done

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
info "downloading manager"
curl -fsSL --retry 3 --max-time 60 -o "$tmp" "$RAW" || { bad "download failed"; exit 1; }
sed -i 's/\r$//' "$tmp"
grep -q 'DARKVPN-GRE-SCRIPT' "$tmp" || { bad "invalid manager file"; exit 1; }
bash -n "$tmp" || { bad "manager has syntax errors"; exit 1; }

ver="$(grep -m1 '^SCRIPT_VER=' "$tmp" | cut -d'"' -f2)"
mkdir -p "$BASE_DIR"
chmod 700 "$BASE_DIR"
[ -f "$DEST" ] && cp -f "$DEST" "$DEST.bak" 2>/dev/null || true
install -m 0755 "$tmp" "$DEST"
printf '%s\n' "$RAW" > "$BASE_DIR/update.url"
chmod 600 "$BASE_DIR/update.url"

ok "installed v${ver:-?} to $DEST"
printf '\n  %srun:%s  darkgre\n\n' "$D" "$N"

if [ -r /dev/tty ]; then exec "$DEST" </dev/tty; fi
