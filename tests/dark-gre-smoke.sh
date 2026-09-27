#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/standalone/dark-gre/dark-gre.sh"
INSTALLER="$ROOT/standalone/dark-gre/install.sh"

bash -n "$SCRIPT"
bash -n "$INSTALLER"


tmpdir="$(mktemp -d)"
trap 'sudo rm -rf "$tmpdir" 2>/dev/null || rm -rf "$tmpdir"' EXIT
sudo env \
  DARK_GRE_RAW_URL="file://$SCRIPT" \
  DARK_GRE_DEST="$tmpdir/darkgre" \
  DARK_GRE_BASE_DIR="$tmpdir/state" \
  DARK_GRE_INSTALL_ONLY=1 \
  bash "$INSTALLER"
sudo test -x "$tmpdir/darkgre"
sudo grep -q 'DARKVPN-GRE-SCRIPT' "$tmpdir/darkgre"
sudo test -s "$tmpdir/state/update.url"

export DARK_GRE_LIB_ONLY=1
# shellcheck disable=SC1090
source "$SCRIPT"

NAME=testgre
LOCAL_PUBLIC=1.2.3.4
REMOTE_PUBLIC=5.6.7.8
LOCAL_TUN=10.77.10.1
REMOTE_TUN=10.77.10.2
PREFIX=30
PROFILE=balanced
MTU=1436
TXQLEN=1000

code="$(pair_code)"
[[ "$code" == DGR1-* ]]
decode_pair "$code"

[ "$P_NAME" = "$NAME" ]
[ "$P_IRAN_PUBLIC" = "$LOCAL_PUBLIC" ]
[ "$P_KHAREJ_PUBLIC" = "$REMOTE_PUBLIC" ]
[ "$P_IRAN_TUN" = "$LOCAL_TUN" ]
[ "$P_KHAREJ_TUN" = "$REMOTE_TUN" ]

grep -q 'item 2 "New tunnel - IRAN" "makes Pair Code"' "$SCRIPT"
grep -q 'item 3 "New tunnel - KHAREJ" "takes Pair Code"' "$SCRIPT"
grep -q 'item p "Pair code" "paste this on the KHAREJ server"' "$SCRIPT"
grep -q 'sect "SETUP"' "$SCRIPT"
grep -q 'sect "OPERATE"' "$SCRIPT"
grep -q 'sect "MAINTENANCE"' "$SCRIPT"
grep -q 'screen_core' "$SCRIPT"

echo "dark-gre rc2 installer + pairing + menu smoke: PASS"
