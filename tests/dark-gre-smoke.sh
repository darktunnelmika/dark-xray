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

# Render runtime units in a temp directory and verify the RC10 lifecycle.
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
! grep -q 'Restart=on-failure' "$UNIT_FILE"
grep -q 'ExecStart=.* watch %i' "$WATCH_UNIT"
grep -q 'Restart=always' "$WATCH_UNIT"
test ! -e "$WATCH_TIMER"
grep -q 'apply_outer_guard' "$RUNNER"
grep -q -- '--pol ipsec' "$RUNNER"
grep -q -- '-p 47' "$RUNNER"
! grep -q 'gre_exists && gre_down' "$RUNNER"
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


# Pair hash must be identical from IRAN and KHAREJ perspectives.
h1="$(pair_shared_hash)"
_tmp_pub="$LOCAL_PUBLIC"; LOCAL_PUBLIC="$REMOTE_PUBLIC"; REMOTE_PUBLIC="$_tmp_pub"
_tmp_tun="$LOCAL_TUN"; LOCAL_TUN="$REMOTE_TUN"; REMOTE_TUN="$_tmp_tun"
h2="$(pair_shared_hash)"
[ "$h1" = "$h2" ]

[ "$(calc_inner_mtu 1500 plain)" = 1472 ]
[ "$(calc_inner_mtu 1500 ipsec)" = 1400 ]

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
grep -q 'item 7 "Pair integrity"' "$SCRIPT"
grep -q 'PAIR_HASH=' "$SCRIPT"
grep -q 'TCPMSS --clamp-mss-to-pmtu' "$SCRIPT"
grep -q 'aes256gcm16' "$SCRIPT"
grep -q 'forceencaps=yes' "$SCRIPT"
grep -q 'fragmentation=yes' "$SCRIPT"
grep -q 'reauth=no' "$SCRIPT"
grep -q 'auto=add' "$SCRIPT"
! grep -q 'auto=start' "$SCRIPT"
grep -q 'flock -n 9' "$SCRIPT"
grep -q 'dpd_mode=restart' "$SCRIPT"
grep -q 'dpd_mode=clear' "$SCRIPT"
grep -q 'overhead=100' "$SCRIPT"
grep -q 'IPSEC_DIR="/etc/ipsec.d/dark-gre"' "$SCRIPT"
grep -q 'IPSEC_SECRETS="/etc/ipsec.dark-gre.secrets"' "$SCRIPT"
! grep -q 'trigger_ipsec()' "$SCRIPT"
grep -q 'WATCH_UNIT="/etc/systemd/system/darkgre-watch@.service"' "$SCRIPT"
grep -q 'watch_loop()' "$SCRIPT"
grep -q 'repair_runtime()' "$SCRIPT"
grep -q 'ensure_ike_initiator()' "$SCRIPT"
grep -q 'ROLE:-.*IRAN' "$SCRIPT"
grep -q 'ike_state()' "$SCRIPT"
grep -q 'darkgre-.*ike-last' "$SCRIPT"
grep -q 'MTU=1400' "$SCRIPT"
grep -q 'sect "SETUP"' "$SCRIPT"
grep -q 'sect "OPERATE"' "$SCRIPT"
grep -q 'sect "MAINTENANCE"' "$SCRIPT"

echo "dark-gre rc10 single initiator + reboot recovery + UDP4500 smoke: PASS"
