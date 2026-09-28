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

runtime="$tmpdir/runtime"
mkdir -p "$runtime"
RUNNER="$runtime/darkgre-runner"
UNIT_FILE="$runtime/darkgre@.service"
WATCH_UNIT="$runtime/darkgre-watch@.service"
WATCH_TIMER="$runtime/darkgre-watch@.timer"
RS_UNIT="$runtime/darkgre-restart@.service"
RS_TIMER="$runtime/darkgre-restart@.timer"
systemctl(){ :; }

write_runner
write_unit

grep -q 'ExecStart=.* arm %i' "$UNIT_FILE"
grep -q 'ExecStart=.* watch %i' "$WATCH_UNIT"
grep -q 'Restart=always' "$WATCH_UNIT"
! grep -q 'strongswan' "$UNIT_FILE"
! grep -q 'strongswan' "$WATCH_UNIT"
test ! -e "$WATCH_TIMER"
grep -q 'sleep 15' "$RUNNER"
grep -q 'DARK_GRE_REPAIR_ONLY=1' "$INSTALLER"

NAME=testgre
LOCAL_PUBLIC=1.2.3.4
REMOTE_PUBLIC=5.6.7.8
LOCAL_TUN=10.77.10.1
REMOTE_TUN=10.77.10.2
PREFIX=30
PROFILE=balanced
MTU_MODE=auto
PATH_MTU=1500
MTU=1472
TXQLEN=1000
GRE_KEY=123456
SECURITY=plain
IPSEC_PSK=""
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
[ "$P_SECURITY" = plain ]
[ -z "$P_IPSEC_PSK" ]
[ "$P_MTU_MODE" = auto ]
[ "$P_MTU" = "$MTU" ]
[ "$P_RESTART" = 6h ]

h1="$(pair_shared_hash)"
_tmp_pub="$LOCAL_PUBLIC"; LOCAL_PUBLIC="$REMOTE_PUBLIC"; REMOTE_PUBLIC="$_tmp_pub"
_tmp_tun="$LOCAL_TUN"; LOCAL_TUN="$REMOTE_TUN"; REMOTE_TUN="$_tmp_tun"
h2="$(pair_shared_hash)"
[ "$h1" = "$h2" ]

[ "$(calc_inner_mtu 1500 plain)" = 1472 ]
[ "$(calc_inner_mtu 1500 ipsec)" = 1472 ]

grep -q 'item 2 "New tunnel - IRAN" "makes Pair Code"' "$SCRIPT"
grep -q 'item 3 "New tunnel - KHAREJ" "takes Pair Code"' "$SCRIPT"
grep -q 'item p "Pair code" "paste this on KHAREJ"' "$SCRIPT"
grep -q 'item p "Apply Pair Code" "re-pair without deleting"' "$SCRIPT"
grep -q 'item 8 "Scheduled restart"' "$SCRIPT"
grep -q 'item s "Speed test"' "$SCRIPT"
grep -q 'item c "Live connections"' "$SCRIPT"
grep -q 'item f "Config fingerprint"' "$SCRIPT"
grep -q 'item v "Show config"' "$SCRIPT"
grep -q 'item 6 "Path MTU scan"' "$SCRIPT"
grep -q 'item 7 "Pair integrity"' "$SCRIPT"
grep -q 'PAIR_HASH=' "$SCRIPT"
grep -q 'TCPMSS --clamp-mss-to-pmtu' "$SCRIPT"
grep -q 'WATCH_UNIT="/etc/systemd/system/darkgre-watch@.service"' "$SCRIPT"
grep -q 'watch_loop()' "$SCRIPT"
grep -q 'repair_runtime()' "$SCRIPT"
grep -q 'SECURITY=plain' "$SCRIPT"
! grep -q 'item 7 "Security" "GRE + IPsec"' "$SCRIPT"
! grep -q 'item i "IPsec / XFRM status"' "$SCRIPT"
grep -q 'sect "SETUP"' "$SCRIPT"
grep -q 'sect "OPERATE"' "$SCRIPT"
grep -q 'sect "MAINTENANCE"' "$SCRIPT"

echo "dark-gre rc12 plain GRE + pair + reboot-runtime smoke: PASS"
