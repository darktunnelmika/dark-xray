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
MTU_MODE=auto
PATH_MTU=1500
MTU=1436
TXQLEN=1000
GRE_KEY=123456
SECURITY=ipsec
IPSEC_PSK=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
RESTART_EVERY=6h

code="$(pair_code)"
[[ "$code" == DGR2-* ]]
decode_pair "$code"

[ "$P_NAME" = "$NAME" ]
[ "$P_IRAN_PUBLIC" = "$LOCAL_PUBLIC" ]
[ "$P_KHAREJ_PUBLIC" = "$REMOTE_PUBLIC" ]
[ "$P_IRAN_TUN" = "$LOCAL_TUN" ]
[ "$P_KHAREJ_TUN" = "$REMOTE_TUN" ]
[ "$P_GRE_KEY" = "$GRE_KEY" ]
[ "$P_SECURITY" = ipsec ]
[ "$P_IPSEC_PSK" = "$IPSEC_PSK" ]
[ "$P_MTU_MODE" = auto ]
[ "$P_MTU" = "$MTU" ]
[ "$P_RESTART" = 6h ]

[ "$(calc_inner_mtu 1500 plain)" = 1472 ]
[ "$(calc_inner_mtu 1500 ipsec)" = 1436 ]

grep -q 'item 2 "New tunnel - IRAN" "makes Pair Code"' "$SCRIPT"
grep -q 'item 3 "New tunnel - KHAREJ" "takes Pair Code"' "$SCRIPT"
grep -q 'item p "Pair code" "paste this on KHAREJ"' "$SCRIPT"
grep -q 'item 7 "Security" "GRE + IPsec"' "$SCRIPT"
grep -q 'item 8 "Scheduled restart"' "$SCRIPT"
grep -q 'item s "Speed test"' "$SCRIPT"
grep -q 'item c "Live connections"' "$SCRIPT"
grep -q 'item f "Config fingerprint"' "$SCRIPT"
grep -q 'item p "Apply Pair Code" "re-pair without deleting"' "$SCRIPT"
grep -q 'item v "Show config"' "$SCRIPT"
grep -q 'item r "Speed responder"' "$SCRIPT"
grep -q 'item 6 "Path MTU scan"' "$SCRIPT"
grep -q 'TCPMSS --clamp-mss-to-pmtu' "$SCRIPT"
grep -q 'aes256gcm16' "$SCRIPT"
grep -q 'timeout 2 ipsec up' "$SCRIPT"
grep -q 'darkgre-watch@.service' "$SCRIPT"
grep -q 'darkgre-watch@.timer' "$SCRIPT"
grep -q 'ExecStart=$RUNNER reconcile %i' "$SCRIPT"
grep -q 'OnUnitActiveSec=5s' "$SCRIPT"
grep -q 'watcher checks every 5s' "$SCRIPT"
! grep -q 'Restart=on-failure' "$SCRIPT"
grep -q 'gre_exists && gre_down' "$SCRIPT"
grep -q 'sect "SETUP"' "$SCRIPT"
grep -q 'sect "OPERATE"' "$SCRIPT"
grep -q 'sect "MAINTENANCE"' "$SCRIPT"

echo "dark-gre rc4 watcher + DGR2 + MTU + security + menu smoke: PASS"
