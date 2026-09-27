#!/usr/bin/env bash
# ==============================================================================
#  DARK VPN · GRE DIRECT
#  Direct GRE tunnel manager
#  Support: @mikakhadm
#
#  FLOW:
#    IRAN creates the tunnel first and generates a Pair Code.
#    KHAREJ pastes the Pair Code and becomes the opposite GRE peer.
#
#  DARKVPN-GRE-SCRIPT
# ==============================================================================

SCRIPT_VER="0.11.0-rc11"
DEV_ID="@mikakhadm"
BASE_DIR="/etc/dark-gre"
TUN_DIR="$BASE_DIR/tunnels"
RUNNER="/usr/local/libexec/darkgre-runner"
UNIT_FILE="/etc/systemd/system/darkgre@.service"
RS_UNIT="/etc/systemd/system/darkgre-restart@.service"
RS_TIMER="/etc/systemd/system/darkgre-restart@.timer"
WATCH_UNIT="/etc/systemd/system/darkgre-watch@.service"
WATCH_TIMER="/etc/systemd/system/darkgre-watch@.timer"
SEC_DIR="$BASE_DIR/security"
IPSEC_DIR="/etc/ipsec.d/dark-gre"
IPSEC_SECRETS="/etc/ipsec.dark-gre.secrets"
OLD_IPSEC_DIR="$SEC_DIR/ipsec.d"
OLD_IPSEC_SECRETS="$SEC_DIR/ipsec.secrets"
UPDATE_URL_FILE="$BASE_DIR/update.url"
SELF_PATH="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "$0")"

if [ "${DARK_GRE_LIB_ONLY:-0}" != 1 ] && [ "${DARK_GRE_REPAIR_ONLY:-0}" != 1 ] && [ ! -t 0 ] && [ -r /dev/tty ]; then
  exec </dev/tty
fi

# ================================================================== UI ======
R=$'\e[38;5;203m'; G=$'\e[38;5;114m'; Y=$'\e[38;5;221m'
C=$'\e[38;5;81m';  M=$'\e[38;5;177m'; W=$'\e[1;97m'
D=$'\e[38;5;244m'; N=$'\e[0m';        BD=$'\e[1m'
L1=$'\e[38;5;33m'; L2=$'\e[38;5;39m'; L3=$'\e[38;5;45m'
L4=$'\e[38;5;51m'; L5=$'\e[38;5;87m'; L6=$'\e[38;5;123m'
BG_OK=$'\e[48;5;22m'; BG_ERR=$'\e[48;5;52m'; BG_WARN=$'\e[48;5;58m'
UIW=62

shopt -s extglob 2>/dev/null
if ! locale charmap 2>/dev/null | grep -qi 'utf-\?8'; then
  for L in C.UTF-8 C.utf8 en_US.UTF-8 en_US.utf8; do
    if locale -a 2>/dev/null | grep -qix "${L//./\\.}"; then export LC_ALL="$L"; break; fi
  done
fi
_probe='é'; [ ${#_probe} -eq 1 ] && UTF_OK=1 || UTF_OK=0

vislen() {
  local s="${1//$'\e['*([0-9;])m/}"
  if [ "$UTF_OK" = 1 ]; then printf '%s' "${#s}"; return; fi
  local b c
  b="$(LC_ALL=C; printf '%s' "$s" | wc -c)"
  c="$(printf '%s' "$s" | LC_ALL=C grep -o $'[\x80-\xbf]' 2>/dev/null | wc -l)"
  printf '%s' $(( b - c ))
}
rep() { local ch="$1" n="$2"; [ "${n:-0}" -gt 0 ] 2>/dev/null || return 0
        printf "${ch}%.0s" $(seq 1 "$n"); }
top()   { printf '  %s╭%s╮%s\n' "$C" "$(rep '─' $((UIW+2)))" "$N"; }
mid()   { printf '  %s├%s┤%s\n' "$C" "$(rep '─' $((UIW+2)))" "$N"; }
bot()   { printf '  %s╰%s╯%s\n' "$C" "$(rep '─' $((UIW+2)))" "$N"; }
row()   { local t="$1" l p; l=$(vislen "$t"); p=$((UIW-l)); ((p<0))&&p=0
          printf '  %s│%s %s%*s %s│%s\n' "$C" "$N" "$t" "$p" "" "$C" "$N"; }
blank() { row ""; }
item()  { row "$(printf '%s%s%s  %s%-22s%s %s%s%s' "$Y" "[$1]" "$N" "$W" "$2" "$N" "$D" "${3:-}" "$N")"; }
kv()    { row "$(printf '%s%-13s%s %s' "$D" "$1" "$N" "$2")"; }
sect()  { row "$(printf '%s%s%s' "$M$BD" "$1" "$N")"; }
badge() { printf '%s %s %s' "$2$BD" "$1" "$N"; }

ok()   { printf '  %s+%s %s\n' "$G" "$N" "$*"; }
bad()  { printf '  %sx%s %s\n' "$R" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
info() { printf '  %s>%s %s\n' "$C" "$N" "$*"; }
dim()  { printf '    %s%s%s\n' "$D" "$*" "$N"; }
dot()  { case "$1" in active) printf '%s*%s' "$G" "$N" ;; failed) printf '%s*%s' "$R" "$N" ;;
                     *) printf '%s*%s' "$D" "$N" ;; esac; }

ask() {
  local p="$1" d="${2:-}" v
  if [ -n "$d" ]; then read -r -p "$(printf '  %s>%s %s %s[%s]%s: ' "$C" "$N" "$p" "$D" "$d" "$N")" v
  else read -r -p "$(printf '  %s>%s %s: ' "$C" "$N" "$p")" v; fi
  ANS="${v:-$d}"
}
yesno() {
  local p="$1" d="$2" v
  read -r -p "$(printf '  %s>%s %s %s[%s]%s: ' "$C" "$N" "$p" "$D" \
      "$([ "$d" = y ] && echo 'Y/n' || echo 'y/N')" "$N")" v
  v="${v:-$d}"; [[ "$v" =~ ^[Yy]$ ]]
}
getkey() { local k; printf '  %s>%s Select: ' "$C" "$N"; read -rsn1 k
           [ -z "$k" ] && k="_"; printf '%s\n\n' "$k"; KEY="$k"; }
pause()  { printf '\n  %spress any key%s' "$D" "$N"; read -rsn1 _; echo; }

gre_core_ready() {
  command -v ip >/dev/null 2>&1 &&
  command -v iptables >/dev/null 2>&1 &&
  { [ -d /sys/module/ip_gre ] || modprobe ip_gre >/dev/null 2>&1; }
}
core_badge() {
  if gre_core_ready; then badge "READY" "$BG_OK$W"; else badge "CHECK" "$BG_WARN$W"; fi
}

header() {
  clear
  top
  row "$(printf '%s██████╗  %s█████╗ %s██████╗ %s██╗  ██╗%s' "$L1" "$L2" "$L3" "$L4" "$N")"
  row "$(printf '%s██╔══██╗%s██╔══██╗%s██╔══██╗%s██║ ██╔╝%s' "$L1" "$L2" "$L3" "$L4" "$N")"
  row "$(printf '%s██║  ██║%s███████║%s██████╔╝%s█████╔╝ %s' "$L2" "$L3" "$L4" "$L5" "$N")"
  row "$(printf '%s██║  ██║%s██╔══██║%s██╔══██╗%s██╔═██╗ %s' "$L2" "$L3" "$L4" "$L5" "$N")"
  row "$(printf '%s██████╔╝%s██║  ██║%s██║  ██║%s██║  ██╗%s' "$L3" "$L4" "$L5" "$L6" "$N")"
  row "$(printf '%s╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝%s' "$D" "$N")"
  row "$(printf '%sG%s R%s E%s   D%s I%s R E C T%s   %sdirect tunnel%s' "$L2" "$L3" "$L4" "$L5" "$L6" "$W$BD" "$N" "$D" "$N")"
  mid
  row "$(printf '%s %sGRE core%s   %sv%s%s   %s%s%s' "$(core_badge)" "$D" "$N" "$D" "$SCRIPT_VER" "$N" "$M" "$DEV_ID" "$N")"
  bot
  [ -n "${1:-}" ] && { echo; printf '  %s>%s %s%s%s\n' "$L4" "$N" "$W$BD" "$1" "$N"; }
  echo
}

# ============================================================== HELPERS ====
need_root(){ [ "$(id -u)" -eq 0 ] || { bad "run as root"; exit 1; }; }
valid_name(){ [[ "$1" =~ ^[A-Za-z0-9_-]{1,24}$ ]]; }
valid_port(){ [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ] && [ "$1" -le 65535 ]; }
valid_ip4(){
  [[ "$1" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  local o; for o in "${BASH_REMATCH[@]:1:4}"; do [ "$o" -le 255 ] || return 1; done
}
b64enc(){ base64 -w0 2>/dev/null || base64 | tr -d '\n'; }
b64dec(){ base64 -d 2>/dev/null; }
sha12(){ printf '%s' "$1" | sha256sum | awk '{print substr($1,1,12)}'; }
pair_shared_hash(){
  local pubs inns pskh
  pubs="$(printf '%s\n%s\n' "$LOCAL_PUBLIC" "$REMOTE_PUBLIC" | sort | paste -sd, -)"
  inns="$(printf '%s\n%s\n' "$LOCAL_TUN" "$REMOTE_TUN" | sort | paste -sd, -)"
  pskh="$(printf '%s' "${IPSEC_PSK:-}" | sha256sum | awk '{print substr($1,1,12)}')"
  printf '%s' "$NAME|$pubs|$inns|$PREFIX|$PROFILE|$MTU|$TXQLEN|$GRE_KEY|${SECURITY:-plain}|$pskh" |
    sha256sum | awk '{print substr($1,1,16)}'
}

primary_ipv4(){
  local dev addr
  dev="$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); exit}}')"
  [ -n "$dev" ] && addr="$(ip -4 -o addr show dev "$dev" scope global 2>/dev/null | awk '{split($4,a,"/"); print a[1]; exit}')"
  printf '%s\n' "${addr:-}"
}
public_ipv4(){
  local loc pub
  loc="$(primary_ipv4)"
  if valid_ip4 "$loc"; then
    echo "$loc"
    return
  fi
  pub="$(curl -4 -fsS --max-time 4 https://api.ipify.org 2>/dev/null || true)"
  valid_ip4 "$pub" && echo "$pub"
}

ensure_deps(){
  local need=0 c
  for c in curl ip iptables systemctl base64 sha256sum awk sed grep ping openssl; do
    command -v "$c" >/dev/null 2>&1 || need=1
  done
  if [ "$need" -ne 0 ]; then
    info "installing GRE dependencies"
    if command -v apt-get >/dev/null 2>&1; then
      apt-get update -qq >/dev/null 2>&1
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl iproute2 iptables kmod coreutils util-linux ca-certificates iputils-ping openssl conntrack >/dev/null 2>&1
    elif command -v dnf >/dev/null 2>&1; then dnf install -y curl iproute iptables kmod coreutils util-linux ca-certificates iputils openssl conntrack-tools >/dev/null 2>&1
    elif command -v yum >/dev/null 2>&1; then yum install -y curl iproute iptables kmod coreutils util-linux ca-certificates iputils openssl conntrack-tools >/dev/null 2>&1
    elif command -v apk >/dev/null 2>&1; then apk add --no-cache bash curl iproute2 iptables kmod coreutils util-linux ca-certificates iputils openssl conntrack-tools >/dev/null 2>&1
    else bad "supported package manager not found"; return 1; fi
  fi
  modprobe ip_gre >/dev/null 2>&1 || true
}
ensure_ipsec_deps(){
  command -v ipsec >/dev/null 2>&1 && return 0
  info "installing strongSwan security core"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq >/dev/null 2>&1
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq strongswan >/dev/null 2>&1
  elif command -v dnf >/dev/null 2>&1; then dnf install -y strongswan >/dev/null 2>&1
  elif command -v yum >/dev/null 2>&1; then yum install -y strongswan >/dev/null 2>&1
  elif command -v apk >/dev/null 2>&1; then apk add --no-cache strongswan >/dev/null 2>&1
  else bad "cannot install strongSwan automatically"; return 1; fi
  command -v ipsec >/dev/null 2>&1
}
install_iperf3(){
  command -v iperf3 >/dev/null 2>&1 && return 0
  info "installing iperf3"
  if command -v apt-get >/dev/null 2>&1; then DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iperf3 >/dev/null 2>&1
  elif command -v dnf >/dev/null 2>&1; then dnf install -y iperf3 >/dev/null 2>&1
  elif command -v yum >/dev/null 2>&1; then yum install -y iperf3 >/dev/null 2>&1
  elif command -v apk >/dev/null 2>&1; then apk add --no-cache iperf3 >/dev/null 2>&1
  else return 1; fi
}

iface_for(){ printf 'dgr%s' "$(printf '%s' "$1" | sha256sum | cut -c1-8)"; }
next_pair(){
  local n
  for n in $(seq 10 250); do
    if ! ip -4 route show | grep -q "10\\.77\\.$n\\.0/30" &&
       ! grep -RqsE "^(LOCAL_TUN|REMOTE_TUN)=10\\.77\\.$n\\." "$TUN_DIR" 2>/dev/null; then
      echo "$n"; return
    fi
  done
  return 1
}
profile_values(){
  case "$1" in stable) TXQLEN=1000 ;; lowping) TXQLEN=500 ;; turbo) TXQLEN=2000 ;; *) PROFILE=balanced; TXQLEN=1000 ;; esac
}
gen_gre_key(){
  local v; v="$(od -An -N4 -tu4 /dev/urandom 2>/dev/null | tr -d ' ')"
  [[ "$v" =~ ^[0-9]+$ ]] && [ "$v" -gt 0 ] && echo "$v" || echo "$(( (RANDOM<<16) ^ RANDOM ^ 1 ))"
}
peer_id_for(){ printf '%s\n%s\n' "$1" "$2" | sort | tr '\n' '|' | sha256sum | cut -c1-12; }
existing_peer_psk(){
  local remote="$1" d
  shopt -s nullglob
  for d in "$TUN_DIR"/*/meta.conf; do
    unset SECURITY IPSEC_PSK REMOTE_PUBLIC
    . "$d" 2>/dev/null || continue
    if [ "${SECURITY:-plain}" = ipsec ] && [ "${REMOTE_PUBLIC:-}" = "$remote" ] && [ -n "${IPSEC_PSK:-}" ]; then
      printf '%s\n' "$IPSEC_PSK"; shopt -u nullglob; return 0
    fi
  done
  shopt -u nullglob; return 1
}
scan_path_mtu(){
  local remote="$1" lo=1100 hi=1472 mid best=0
  ping -4 -c 1 -W 1 "$remote" >/dev/null 2>&1 || { echo 0; return 1; }
  while [ "$lo" -le "$hi" ]; do
    mid=$(( (lo+hi)/2 ))
    if ping -4 -M do -s "$mid" -c 1 -W 1 "$remote" >/dev/null 2>&1; then best="$mid"; lo=$((mid+1)); else hi=$((mid-1)); fi
  done
  [ "$best" -gt 0 ] || { echo 0; return 1; }
  echo $((best+28))
}
calc_inner_mtu(){
  local p="$1" sec="$2" overhead mtu
  [ "$sec" = ipsec ] && overhead=100 || overhead=28
  mtu=$((p-overhead))
  [ "$mtu" -lt 1280 ] && mtu=1280
  [ "$sec" = ipsec ] && [ "$mtu" -gt 1400 ] && mtu=1400
  [ "$sec" != ipsec ] && [ "$mtu" -gt 1472 ] && mtu=1472
  echo "$mtu"
}
choose_security(){
  echo; top; sect "SECURITY"; blank
  item 1 "GRE + IPsec" "AES-256-GCM / IKEv2 - recommended"
  item 2 "Plain GRE" "fastest - no encryption"
  bot; echo; getkey
  case "$KEY" in
    2) SECURITY=plain; IPSEC_PSK="" ;;
    *) SECURITY=ipsec
       ensure_ipsec_deps || { bad "strongSwan install failed"; return 1; }
       IPSEC_PSK="$(existing_peer_psk "$REMOTE_PUBLIC" 2>/dev/null || true)"
       [ -n "$IPSEC_PSK" ] || IPSEC_PSK="$(openssl rand -hex 32)" ;;
  esac
}
choose_mtu(){
  echo; top; sect "MTU"; blank
  item 1 "Auto Scan" "Path MTU scan - recommended"
  item 2 "Safe" "conservative"
  item 3 "Maximum" "assume clean 1500 path"
  item 4 "Custom" "manual value"
  bot; echo; getkey
  case "$KEY" in
    2) MTU_MODE=safe; PATH_MTU=1500; [ "$SECURITY" = ipsec ] && MTU=1360 || MTU=1400 ;;
    3) MTU_MODE=maximum; PATH_MTU=1500; MTU="$(calc_inner_mtu 1500 "$SECURITY")" ;;
    4) MTU_MODE=custom; PATH_MTU=0
       ask "inner MTU" "$([ "$SECURITY" = ipsec ] && echo 1400 || echo 1450)"
       [[ "$ANS" =~ ^[0-9]+$ ]] && [ "$ANS" -ge 1200 ] && [ "$ANS" -le 1472 ] || { bad "invalid MTU"; return 1; }
       MTU="$ANS" ;;
    *) MTU_MODE=auto
       info "scanning Path MTU to $REMOTE_PUBLIC"
       PATH_MTU="$(scan_path_mtu "$REMOTE_PUBLIC" 2>/dev/null || echo 0)"
       if [ "${PATH_MTU:-0}" -ge 1280 ]; then MTU="$(calc_inner_mtu "$PATH_MTU" "$SECURITY")"; ok "Path MTU $PATH_MTU -> GRE MTU $MTU"
       else PATH_MTU=0; [ "$SECURITY" = ipsec ] && MTU=1360 || MTU=1400; warn "PMTU scan unavailable - safe MTU $MTU selected"; fi ;;
  esac
}
pick_restart(){
  echo; top; sect "SCHEDULED RESTART"; blank
  row "$(printf '%sclears a degraded tunnel on a fixed interval%s' "$D" "$N")"
  item 1 "off" ""; item 2 "1h" "recommended"; item 3 "6h" ""; item 4 "12h" ""; item 5 "24h" ""
  bot; echo; getkey
  case "$KEY" in 2) RESTART_EVERY=1h;; 3) RESTART_EVERY=6h;; 4) RESTART_EVERY=12h;; 5) RESTART_EVERY=24h;; *) RESTART_EVERY=off;; esac
}

write_runner(){
  install -d -m 0755 "$(dirname "$RUNNER")"
  cat >"$RUNNER" <<'RUNNER_EOF'
#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="/etc/dark-gre"; TUN_DIR="$BASE_DIR/tunnels"
action="${1:-up}"; name="${2:-}"
[ -n "$name" ] || { echo "missing tunnel name" >&2; exit 2; }
conf="$TUN_DIR/$name/meta.conf"; [ -r "$conf" ] || { echo "missing $conf" >&2; exit 3; }
. "$conf"
SECURITY="${SECURITY:-plain}"; IPSEC_PSK="${IPSEC_PSK:-}"; GRE_KEY="${GRE_KEY:-0}"
MTU_MODE="${MTU_MODE:-custom}"; PATH_MTU="${PATH_MTU:-0}"; RESTART_EVERY="${RESTART_EVERY:-off}"
PEER_ID="${PEER_ID:-$(printf '%s\n%s\n' "$LOCAL_PUBLIC" "$REMOTE_PUBLIC" | sort | tr '\n' '|' | sha256sum | cut -c1-12)}"
nat_chain="DGRN_${ID}"; post_chain="DGRP_${ID}"; fw_chain="DGRF_${ID}"; mss_chain="DGRM_${ID}"
sec_in_chain="DGRI_${ID}"; sec_out_chain="DGRO_${ID}"
remove_chain(){ local table="$1" chain="$2" hook="$3"; iptables -t "$table" -D "$hook" -j "$chain" 2>/dev/null || true; iptables -t "$table" -F "$chain" 2>/dev/null || true; iptables -t "$table" -X "$chain" 2>/dev/null || true; }
remove_data_fw(){
  remove_chain nat "$nat_chain" PREROUTING
  remove_chain nat "$post_chain" POSTROUTING
  remove_chain filter "$fw_chain" FORWARD
  iptables -t mangle -D FORWARD -j "$mss_chain" 2>/dev/null || true
  iptables -t mangle -D OUTPUT -j "$mss_chain" 2>/dev/null || true
  iptables -t mangle -F "$mss_chain" 2>/dev/null || true
  iptables -t mangle -X "$mss_chain" 2>/dev/null || true
}
remove_outer_guard(){
  iptables -D INPUT -j "$sec_in_chain" 2>/dev/null || true
  iptables -D OUTPUT -j "$sec_out_chain" 2>/dev/null || true
  iptables -F "$sec_in_chain" 2>/dev/null || true
  iptables -F "$sec_out_chain" 2>/dev/null || true
  iptables -X "$sec_in_chain" 2>/dev/null || true
  iptables -X "$sec_out_chain" 2>/dev/null || true
}
remove_fw(){ remove_data_fw; remove_outer_guard; }
apply_mss(){
  iptables -t mangle -N "$mss_chain" 2>/dev/null || true
  iptables -t mangle -C FORWARD -j "$mss_chain" 2>/dev/null || iptables -t mangle -I FORWARD 1 -j "$mss_chain"
  iptables -t mangle -C OUTPUT -j "$mss_chain" 2>/dev/null || iptables -t mangle -I OUTPUT 1 -j "$mss_chain"
  iptables -t mangle -C "$mss_chain" -o "$IFNAME" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null ||
    iptables -t mangle -A "$mss_chain" -o "$IFNAME" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
}
apply_outer_guard(){
  [ "${SECURITY:-plain}" = ipsec ] || { remove_outer_guard; return 0; }
  modprobe xt_policy 2>/dev/null || true
  iptables -m policy -h >/dev/null 2>&1 || { echo "xt_policy unavailable; refusing insecure GRE" >&2; return 1; }

  iptables -N "$sec_in_chain" 2>/dev/null || true
  iptables -N "$sec_out_chain" 2>/dev/null || true
  iptables -C INPUT -j "$sec_in_chain" 2>/dev/null || iptables -I INPUT 1 -j "$sec_in_chain"
  iptables -C OUTPUT -j "$sec_out_chain" 2>/dev/null || iptables -I OUTPUT 1 -j "$sec_out_chain"

  iptables -C "$sec_in_chain" -p 47 -s "$REMOTE_PUBLIC" -m policy --dir in --pol ipsec -j RETURN 2>/dev/null ||
    iptables -A "$sec_in_chain" -p 47 -s "$REMOTE_PUBLIC" -m policy --dir in --pol ipsec -j RETURN
  iptables -C "$sec_in_chain" -p 47 -s "$REMOTE_PUBLIC" -j DROP 2>/dev/null ||
    iptables -A "$sec_in_chain" -p 47 -s "$REMOTE_PUBLIC" -j DROP
  iptables -C "$sec_in_chain" -j RETURN 2>/dev/null || iptables -A "$sec_in_chain" -j RETURN

  iptables -C "$sec_out_chain" -p 47 -d "$REMOTE_PUBLIC" -m policy --dir out --pol ipsec -j RETURN 2>/dev/null ||
    iptables -A "$sec_out_chain" -p 47 -d "$REMOTE_PUBLIC" -m policy --dir out --pol ipsec -j RETURN
  iptables -C "$sec_out_chain" -p 47 -d "$REMOTE_PUBLIC" -j DROP 2>/dev/null ||
    iptables -A "$sec_out_chain" -p 47 -d "$REMOTE_PUBLIC" -j DROP
  iptables -C "$sec_out_chain" -j RETURN 2>/dev/null || iptables -A "$sec_out_chain" -j RETURN
}
apply_fw(){
  remove_data_fw
  apply_mss
  [ "$ROLE" = IRAN ] || return 0
  iptables -t nat -N "$nat_chain" 2>/dev/null || true
  iptables -t nat -N "$post_chain" 2>/dev/null || true
  iptables -t filter -N "$fw_chain" 2>/dev/null || true
  iptables -t nat -C PREROUTING -j "$nat_chain" 2>/dev/null || iptables -t nat -I PREROUTING 1 -j "$nat_chain"
  iptables -t nat -C POSTROUTING -j "$post_chain" 2>/dev/null || iptables -t nat -I POSTROUTING 1 -j "$post_chain"
  iptables -t filter -C FORWARD -j "$fw_chain" 2>/dev/null || iptables -t filter -I FORWARD 1 -j "$fw_chain"
  iptables -t filter -A "$fw_chain" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  while IFS=: read -r proto listen target; do
    [ -n "${proto:-}" ] || continue
    case "$proto" in
      tcp|udp)
        iptables -t nat -A "$nat_chain" -p "$proto" --dport "$listen" -j DNAT --to-destination "$REMOTE_TUN:$target"
        iptables -t nat -A "$post_chain" -p "$proto" -d "$REMOTE_TUN" --dport "$target" -o "$IFNAME" -j MASQUERADE
        iptables -t filter -A "$fw_chain" -p "$proto" -d "$REMOTE_TUN" --dport "$target" -o "$IFNAME" -j ACCEPT
        ;;
    esac
  done <"$TUN_DIR/$name/ports.list"
}
ipsec_ready(){
  [ "${SECURITY:-plain}" != ipsec ] && return 0
  command -v ipsec >/dev/null 2>&1 || return 1
  ip xfrm state 2>/dev/null | grep -q "$REMOTE_PUBLIC" &&
  ip xfrm policy 2>/dev/null | grep -q "$REMOTE_PUBLIC"
}
gre_exists(){ ip link show "$IFNAME" >/dev/null 2>&1; }
gre_down(){
  remove_fw
  ip link set dev "$IFNAME" down 2>/dev/null || true
  ip tunnel del "$IFNAME" 2>/dev/null || true
}
gre_up(){
  modprobe ip_gre 2>/dev/null || true
  apply_outer_guard || return 1
  if ! gre_exists; then
    if [ "$GRE_KEY" = 0 ] || [ -z "$GRE_KEY" ]; then
      ip tunnel add "$IFNAME" mode gre local "$LOCAL_PUBLIC" remote "$REMOTE_PUBLIC" ttl 64
    else
      ip tunnel add "$IFNAME" mode gre local "$LOCAL_PUBLIC" remote "$REMOTE_PUBLIC" ttl 64 key "$GRE_KEY"
    fi
    ip addr add "$LOCAL_TUN/$PREFIX" dev "$IFNAME"
  fi
  ip link set dev "$IFNAME" mtu "$MTU" txqueuelen "$TXQLEN" up
  sysctl -q -w net.ipv4.ip_forward=1 >/dev/null
  apply_fw
}
reconcile(){
  if ! gre_exists; then
    gre_up
    return $?
  fi
  apply_outer_guard || return 1
  return 0
}
watch_loop(){
  while :; do
    reconcile || true
    sleep 15
  done
}
case "$action" in
  arm|up) reconcile ;;
  reconcile) reconcile ;;
  watch) watch_loop ;;
  down) gre_down ;;
  reload-fw) gre_exists && apply_fw || true ;;
  *) echo "unknown action: $action" >&2; exit 4 ;;
esac
RUNNER_EOF
  chmod 0755 "$RUNNER"
}

write_unit(){
  cat >"$UNIT_FILE" <<UNIT_EOF
[Unit]
Description=DARK GRE Direct tunnel %i
After=network-online.target strongswan-starter.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$RUNNER arm %i
ExecStop=$RUNNER down %i
TimeoutStartSec=10
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
UNIT_EOF

  cat >"$WATCH_UNIT" <<UNIT_EOF
[Unit]
Description=DARK GRE peer/security watcher %i
After=network-online.target strongswan-starter.service darkgre@%i.service
Requires=darkgre@%i.service
PartOf=darkgre@%i.service

[Service]
Type=simple
ExecStart=$RUNNER watch %i
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT_EOF

  cat >"$RS_UNIT" <<'EOF'
[Unit]
Description=Restart DARK GRE tunnel %i
[Service]
Type=oneshot
ExecStart=/bin/systemctl restart darkgre@%i.service
EOF
  cat >"$RS_TIMER" <<'EOF'
[Unit]
Description=Scheduled restart for DARK GRE tunnel %i
[Timer]
OnBootSec=6h
OnUnitActiveSec=6h
Persistent=true
Unit=darkgre-restart@%i.service
[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
}

ensure_system(){
  mkdir -p "$TUN_DIR" "$BASE_DIR" "$SEC_DIR" "$IPSEC_DIR"
  chmod 700 "$BASE_DIR" "$TUN_DIR" "$SEC_DIR"
  chmod 755 "$IPSEC_DIR"
  write_runner; write_unit
  printf 'net.ipv4.ip_forward=1\n' >/etc/sysctl.d/99-dark-gre.conf
  sysctl -q --system >/dev/null 2>&1 || true
}
set_restart_timer(){
  local name="$1" every="$2" dir="/etc/systemd/system/darkgre-restart@$1.timer.d"
  if [ "$every" = off ]; then systemctl disable --now "darkgre-restart@$name.timer" >/dev/null 2>&1 || true; rm -rf "$dir"; systemctl daemon-reload >/dev/null 2>&1 || true; return; fi
  mkdir -p "$dir"; printf '[Timer]\nOnUnitActiveSec=\nOnUnitActiveSec=%s\nOnBootSec=\nOnBootSec=%s\n' "$every" "$every" >"$dir/interval.conf"
  systemctl daemon-reload >/dev/null 2>&1; systemctl enable --now "darkgre-restart@$name.timer" >/dev/null 2>&1
}
security_sync_all(){
  command -v ipsec >/dev/null 2>&1 || return 0

  mkdir -p "$IPSEC_DIR"
  chmod 755 "$IPSEC_DIR"
  rm -f "$IPSEC_DIR"/*.conf 2>/dev/null || true
  : >"$IPSEC_SECRETS"
  chmod 600 "$IPSEC_SECRETS"

  local f pid conn start_mode dpd_mode close_mode
  declare -A done=()
  shopt -s nullglob
  for f in "$TUN_DIR"/*/meta.conf; do
    unset SECURITY IPSEC_PSK LOCAL_PUBLIC REMOTE_PUBLIC PEER_ID ROLE
    . "$f" 2>/dev/null || continue
    [ "${SECURITY:-plain}" = ipsec ] || continue
    [ -n "${IPSEC_PSK:-}" ] || continue

    pid="${PEER_ID:-$(peer_id_for "$LOCAL_PUBLIC" "$REMOTE_PUBLIC")}"
    [ -n "${done[$pid]:-}" ] && continue
    done[$pid]=1
    conn="darkgre-$pid"
    if [ "$ROLE" = IRAN ]; then
      start_mode=start
      dpd_mode=restart
      close_mode=restart
    else
      start_mode=add
      dpd_mode=clear
      close_mode=clear
    fi

    cat >"$IPSEC_DIR/$pid.conf" <<EOF
conn $conn
  keyexchange=ikev2
  type=transport
  authby=psk
  left=$LOCAL_PUBLIC
  right=$REMOTE_PUBLIC
  leftid=$LOCAL_PUBLIC
  rightid=$REMOTE_PUBLIC
  leftprotoport=47
  rightprotoport=47
  ike=aes256gcm16-prfsha256-modp2048!
  esp=aes256gcm16!
  forceencaps=yes
  fragmentation=yes
  reauth=no
  rekey=yes
  dpdaction=$dpd_mode
  closeaction=$close_mode
  dpddelay=30s
  keyingtries=%forever
  mobike=no
  auto=$start_mode
EOF
    chmod 600 "$IPSEC_DIR/$pid.conf"
    printf '%s %s : PSK "%s"\n' "$LOCAL_PUBLIC" "$REMOTE_PUBLIC" "$IPSEC_PSK" >>"$IPSEC_SECRETS"
  done
  shopt -u nullglob

  # Remove all old DARK GRE includes, including the AppArmor-blocked RC3/RC4 path.
  sed -i '\|include /etc/dark-gre/security/ipsec.d/\*.conf|d;\|include /etc/ipsec.d/dark-gre/\*.conf|d' /etc/ipsec.conf 2>/dev/null || true
  sed -i '\|include /etc/dark-gre/security/ipsec.secrets|d;\|include /etc/ipsec.dark-gre.secrets|d' /etc/ipsec.secrets 2>/dev/null || true

  printf '\n# DARK GRE managed\ninclude /etc/ipsec.d/dark-gre/*.conf\n' >>/etc/ipsec.conf
  printf '\n# DARK GRE managed\ninclude /etc/ipsec.dark-gre.secrets\n' >>/etc/ipsec.secrets

  # Old files are no longer consumed; remove them so AppArmor errors cannot recur.
  rm -rf "$OLD_IPSEC_DIR" "$OLD_IPSEC_SECRETS" 2>/dev/null || true

  systemctl enable --now strongswan-starter >/dev/null 2>&1 || systemctl enable --now strongswan >/dev/null 2>&1 || true
  ipsec reload >/dev/null 2>&1 || true
  ipsec rereadsecrets >/dev/null 2>&1 || true

  # RC11: strongSwan is the only IKE initiator; IRAN=start, KHAREJ=add.
}

migrate_existing_tunnels(){
  local f
  shopt -s nullglob
  for f in "$TUN_DIR"/*/meta.conf; do
    grep -q '^SECURITY=' "$f" || printf 'SECURITY="plain"\n' >>"$f"
    grep -q '^IPSEC_PSK=' "$f" || printf 'IPSEC_PSK=""\n' >>"$f"
    grep -q '^GRE_KEY=' "$f" || printf 'GRE_KEY="0"\n' >>"$f"
    grep -q '^MTU_MODE=' "$f" || printf 'MTU_MODE="custom"\n' >>"$f"
    grep -q '^PATH_MTU=' "$f" || printf 'PATH_MTU="0"\n' >>"$f"
    grep -q '^RESTART_EVERY=' "$f" || printf 'RESTART_EVERY="off"\n' >>"$f"
    grep -q '^PAIR_HASH=' "$f" || printf 'PAIR_HASH=""\n' >>"$f"

    # RC9 migration: existing secure auto tunnels from RC7/RC8 used MTU 1436.
    unset SECURITY MTU_MODE MTU PATH_MTU NAME LOCAL_PUBLIC REMOTE_PUBLIC LOCAL_TUN REMOTE_TUN PREFIX PROFILE TXQLEN GRE_KEY IPSEC_PSK
    . "$f" 2>/dev/null || continue
    if [ "${SECURITY:-plain}" = ipsec ] && [ "${MTU_MODE:-custom}" = auto ] && [ "${MTU:-0}" -gt 1400 ] 2>/dev/null; then
      sed -i 's|^MTU=.*|MTU=1400|' "$f"
      MTU=1400
    fi

    if ! grep -q '^PEER_ID=' "$f"; then
      unset LOCAL_PUBLIC REMOTE_PUBLIC
      . "$f" 2>/dev/null || continue
      printf 'PEER_ID="%s"\n' "$(peer_id_for "$LOCAL_PUBLIC" "$REMOTE_PUBLIC")" >>"$f"
    fi

    # Refresh the local fingerprint; mismatched peers will intentionally show different hashes.
    . "$f" 2>/dev/null || continue
    local ph
    ph="$(pair_shared_hash)"
    if grep -q '^PAIR_HASH=' "$f"; then
      sed -i "s|^PAIR_HASH=.*|PAIR_HASH=$(printf %q "$ph")|" "$f"
    else
      printf 'PAIR_HASH=%q\n' "$ph" >>"$f"
    fi
  done
  shopt -u nullglob
}

PARTIAL_TUNNEL=""
discard_partial(){ [ -n "$PARTIAL_TUNNEL" ] || return 0; local p="$PARTIAL_TUNNEL"; PARTIAL_TUNNEL=""; [ -s "$TUN_DIR/$p/meta.conf" ] || rm -rf "${TUN_DIR:?}/$p"; }
on_interrupt(){ trap - INT TERM; echo; discard_partial; warn "cancelled"; exit 130; }
sweep_partials(){
  local d; shopt -s nullglob
  for d in "$TUN_DIR"/*; do [ -d "$d" ] || continue; [ -s "$d/meta.conf" ] || { warn "removing incomplete tunnel $(basename "$d")"; rm -rf "$d"; }; done
  shopt -u nullglob
}

save_meta(){
  local dir="$1"; shift
  mkdir -p "$dir"; chmod 700 "$dir"
  : >"$dir/meta.conf"
  local kv key val
  for kv in "$@"; do key="${kv%%=*}"; val="${kv#*=}"; printf '%s=%q\n' "$key" "$val" >>"$dir/meta.conf"; done
  chmod 600 "$dir/meta.conf"
  [ -f "$dir/ports.list" ] || : >"$dir/ports.list"
  chmod 600 "$dir/ports.list"
}

service_start(){
  local n="$1"
  systemctl enable "darkgre@$n.service" >/dev/null 2>&1 || return 1
  systemctl start "darkgre@$n.service" >/dev/null 2>&1 || return 1
  systemctl disable --now "darkgre-watch@$n.timer" >/dev/null 2>&1 || true
  systemctl enable --now "darkgre-watch@$n.service" >/dev/null 2>&1 || return 1
  "$RUNNER" reconcile "$n" >/dev/null 2>&1 || true
  return 0
}
service_restart(){
  local n="$1"
  systemctl restart "darkgre@$n.service" >/dev/null 2>&1 || return 1
  systemctl disable --now "darkgre-watch@$n.timer" >/dev/null 2>&1 || true
  systemctl enable --now "darkgre-watch@$n.service" >/dev/null 2>&1 || true
  "$RUNNER" reconcile "$n" >/dev/null 2>&1 || true
}
service_stop(){
  local n="$1"
  systemctl disable --now "darkgre-watch@$n.timer" >/dev/null 2>&1 || true
  systemctl disable --now "darkgre-watch@$n.service" >/dev/null 2>&1 || true
  systemctl stop "darkgre@$n.service" >/dev/null 2>&1 || true
}
service_state(){
  local n="$1" d="$TUN_DIR/$1" st sec
  st="$(systemctl is-active "darkgre@$n.service" 2>/dev/null || true)"
  [ "$st" = active ] || { printf '%s\n' "${st:-inactive}"; return; }
  [ -r "$d/meta.conf" ] || { echo active; return; }
  . "$d/meta.conf"; sec="${SECURITY:-plain}"
  if ip link show "$IFNAME" >/dev/null 2>&1; then
    [ "$sec" = ipsec ] && ! ipsec_state "$n" 2>/dev/null | grep -q encrypted && { echo securing; return; }
    echo active
  elif [ "$sec" = ipsec ]; then echo waiting
  else echo armed
  fi
}

svc_uptime_short(){
  local ts t n d
  ts="$(systemctl show "darkgre@$1.service" -p ActiveEnterTimestamp --value 2>/dev/null)"
  [ -n "$ts" ] || { echo "-"; return; }
  t="$(date -d "$ts" +%s 2>/dev/null)" || { echo "-"; return; }; n="$(date +%s)"; d=$((n-t)); [ "$d" -lt 0 ] && { echo "-"; return; }
  if [ "$d" -ge 86400 ]; then printf '%dd%02dh' $((d/86400)) $((d%86400/3600))
  elif [ "$d" -ge 3600 ]; then printf '%dh%02dm' $((d/3600)) $((d%3600/60))
  else printf '%dm%02ds' $((d/60)) $((d%60)); fi
}
human_bytes(){
  local b="${1:-0}"
  if [ "$b" -ge 1073741824 ] 2>/dev/null; then printf '%d.%01dG' $((b/1073741824)) $(((b%1073741824)*10/1073741824))
  elif [ "$b" -ge 1048576 ] 2>/dev/null; then printf '%d.%01dM' $((b/1048576)) $(((b%1048576)*10/1048576))
  elif [ "$b" -ge 1024 ] 2>/dev/null; then printf '%dK' $((b/1024))
  else printf '%sB' "$b"; fi
}
tunnel_traffic(){
  local n="$1" d="$TUN_DIR/$1" rx=0 tx=0
  [ -r "$d/meta.conf" ] || { echo "0 0"; return; }; . "$d/meta.conf"
  [ -d "/sys/class/net/$IFNAME" ] || { echo "0 0"; return; }
  rx="$(cat "/sys/class/net/$IFNAME/statistics/rx_bytes" 2>/dev/null || echo 0)"
  tx="$(cat "/sys/class/net/$IFNAME/statistics/tx_bytes" 2>/dev/null || echo 0)"
  echo "$rx $tx"
}
ipsec_state(){
  local d="$TUN_DIR/$1"; [ -r "$d/meta.conf" ] || { echo "-"; return; }; . "$d/meta.conf"
  [ "${SECURITY:-plain}" = ipsec ] || { echo plain; return; }
  command -v ipsec >/dev/null 2>&1 || { echo missing; return; }
  ipsec status "darkgre-$PEER_ID" 2>/dev/null | grep -qi ESTABLISHED && echo encrypted || echo down
}
config_fingerprint(){
  local n="$1" d="$TUN_DIR/$1" pubs inns ph sec
  . "$d/meta.conf"
  pubs="$(printf '%s\n%s\n' "$LOCAL_PUBLIC" "$REMOTE_PUBLIC" | sort | paste -sd, -)"
  inns="$(printf '%s\n%s\n' "$LOCAL_TUN" "$REMOTE_TUN" | sort | paste -sd, -)"
  ph="$(printf '%s' "${IPSEC_PSK:-}" | sha256sum | cut -c1-12)"
  sec="${SECURITY:-plain}"
  printf '%s' "$pubs|$inns|$PREFIX|$PROFILE|$MTU|$GRE_KEY|$sec|$ph" | sha256sum | cut -c1-20
}
pair_integrity_check(){
  local n="$1" d="$TUN_DIR/$1" rx tx xbytes
  [ -r "$d/meta.conf" ] || return 1
  . "$d/meta.conf"
  read -r rx tx <<<"$(tunnel_traffic "$n")"
  xbytes="$(ip -s xfrm state 2>/dev/null | awk -v peer="$REMOTE_PUBLIC" '
    $0 ~ ("dst " peer) {hit=1}
    hit && /bytes/ {for(i=1;i<=NF;i++) if($i=="bytes"){sum+=$(i+1); hit=0}}
    END{print sum+0}')"
  echo
  top; sect "PAIR INTEGRITY"; blank
  kv "pair hash" "$W${PAIR_HASH:-legacy}$N"
  kv "gre key" "$W$GRE_KEY$N"
  kv "GRE rx/tx" "$W$(human_bytes "$rx") / $(human_bytes "$tx")$N"
  kv "XFRM bytes" "$W${xbytes:-0}$N"
  if [ "${SECURITY:-plain}" = ipsec ] && [ "${xbytes:-0}" -gt 0 ] 2>/dev/null && [ "${rx:-0}" -eq 0 ] 2>/dev/null; then
    blank
    row "$(printf '%sIPsec is moving packets but GRE RX is zero.%s' "$R" "$N")"
    row "$(printf '%slikely stale/mismatched GRE key or Pair Code.%s' "$Y" "$N")"
    row "$(printf '%sKHAREJ: Manage -> PAIRING -> Apply Pair Code%s' "$D" "$N")"
  elif ping -c1 -W1 "$REMOTE_TUN" >/dev/null 2>&1; then
    blank; row "$(printf '%spair data plane looks healthy%s' "$G" "$N")"
  else
    blank; row "$(printf '%spair not healthy yet - inspect XFRM and peer fingerprint%s' "$Y" "$N")"
  fi
  bot
}
health_check(){
  header "HEALTH CHECK"
  local d st rx tx okn=0 badn=0
  shopt -s nullglob
  for d in "$TUN_DIR"/*; do
    [ -r "$d/meta.conf" ] || continue; . "$d/meta.conf"; st="$(service_state "$NAME")"
    read -r rx tx <<<"$(tunnel_traffic "$NAME")"
    if [ "$st" = active ] && ip link show "$IFNAME" >/dev/null 2>&1 && ping -c1 -W1 "$REMOTE_TUN" >/dev/null 2>&1; then
      ok "$NAME  $ROLE  peer ok  mtu=$MTU  security=$(ipsec_state "$NAME")  traffic $(human_bytes "$rx")/$(human_bytes "$tx")"; okn=$((okn+1))
    else
      bad "$NAME  state=$st  peer=$REMOTE_TUN  security=$(ipsec_state "$NAME")"; badn=$((badn+1))
    fi
  done
  shopt -u nullglob
  echo; info "healthy $okn   problem $badn"
}


choose_profile(){
  echo; top; sect "PROFILE"; blank
  row "$(printf '%sprofile tunes queueing; MTU is selected separately%s' "$D" "$N")"
  item 1 "Balanced" "recommended"; item 2 "Stable" "lossy paths"; item 3 "Low Ping" "interactive / gaming"; item 4 "Turbo" "high throughput"
  bot; echo; getkey
  case "$KEY" in 2) PROFILE=stable;; 3) PROFILE=lowping;; 4) PROFILE=turbo;; *) PROFILE=balanced;; esac
  profile_values "$PROFILE"
}

add_port_noninteractive(){
  local name="$1" proto="$2" listen="$3" target="$4" file="$TUN_DIR/$name/ports.list"
  valid_port "$listen" && valid_port "$target" || return 1
  case "$proto" in tcp|udp) ;; *) return 1;; esac
  grep -qx "$proto:$listen:$target" "$file" 2>/dev/null || echo "$proto:$listen:$target" >>"$file"
}

prompt_initial_ports(){
  local name="$1" p arr added
  echo; top; sect "USER PORTS"; blank
  row "$(printf '%sone port, or several separated by commas%s' "$D" "$N")"
  row "$(printf '%sexample:  1185,443,2087%s' "$D" "$N")"
  row "$(printf '%ssame port on KHAREJ is used by default%s' "$D" "$N")"
  bot; echo
  while :; do
    ask "ports"
    IFS=', ' read -r -a arr <<<"$ANS"
    : >"$TUN_DIR/$name/ports.list"
    added=0
    for p in "${arr[@]}"; do
      valid_port "$p" || continue
      add_port_noninteractive "$name" tcp "$p" "$p" && added=$((added+1))
    done
    [ "$added" -gt 0 ] && break
    bad "enter at least one valid port"
  done
  if yesno "these services also need UDP?" n; then
    for p in "${arr[@]}"; do
      valid_port "$p" && add_port_noninteractive "$name" udp "$p" "$p"
    done
  fi
}

pair_code(){
  local payload sum
  payload="2|$NAME|$LOCAL_PUBLIC|$REMOTE_PUBLIC|$LOCAL_TUN|$REMOTE_TUN|$PREFIX|$PROFILE|${MTU_MODE:-auto}|${PATH_MTU:-0}|$MTU|$TXQLEN|$GRE_KEY|${SECURITY:-plain}|${IPSEC_PSK:-}|${RESTART_EVERY:-off}"
  sum="$(sha12 "$payload")"; printf 'DGR2-%s-%s\n' "$sum" "$(printf '%s' "$payload" | b64enc)"
}
decode_pair(){
  local code="$1" sum enc payload calc ver
  P_SECURITY=plain; P_IPSEC_PSK=""; P_MTU_MODE=custom; P_PATH_MTU=0; P_RESTART=off; P_GRE_KEY=0
  if [[ "$code" =~ ^DGR2-([0-9a-f]{12})-(.+)$ ]]; then
    sum="${BASH_REMATCH[1]}"; enc="${BASH_REMATCH[2]}"; payload="$(printf '%s' "$enc" | b64dec)" || return 1; calc="$(sha12 "$payload")"; [ "$calc" = "$sum" ] || return 1
    IFS='|' read -r ver P_NAME P_IRAN_PUBLIC P_KHAREJ_PUBLIC P_IRAN_TUN P_KHAREJ_TUN P_PREFIX P_PROFILE P_MTU_MODE P_PATH_MTU P_MTU P_TXQLEN P_GRE_KEY P_SECURITY P_IPSEC_PSK P_RESTART <<<"$payload"
    [ "$ver" = 2 ] || return 1
  elif [[ "$code" =~ ^DGR1-([0-9a-f]{12})-(.+)$ ]]; then
    sum="${BASH_REMATCH[1]}"; enc="${BASH_REMATCH[2]}"; payload="$(printf '%s' "$enc" | b64dec)" || return 1; calc="$(sha12 "$payload")"; [ "$calc" = "$sum" ] || return 1
    IFS='|' read -r ver P_NAME P_IRAN_PUBLIC P_KHAREJ_PUBLIC P_IRAN_TUN P_KHAREJ_TUN P_PREFIX P_PROFILE P_MTU P_TXQLEN <<<"$payload"
    [ "$ver" = 1 ] || return 1
  else return 1; fi
  valid_name "$P_NAME" && valid_ip4 "$P_IRAN_PUBLIC" && valid_ip4 "$P_KHAREJ_PUBLIC" && valid_ip4 "$P_IRAN_TUN" && valid_ip4 "$P_KHAREJ_TUN" &&
  [[ "$P_PREFIX" =~ ^[0-9]+$ ]] && [ "$P_PREFIX" -ge 8 ] && [ "$P_PREFIX" -le 32 ] &&
  [[ "$P_MTU" =~ ^[0-9]+$ ]] && [ "$P_MTU" -ge 1200 ] && [ "$P_MTU" -le 1476 ] &&
  [[ "$P_TXQLEN" =~ ^[0-9]+$ ]] && [[ "$P_PROFILE" =~ ^(balanced|stable|lowping|turbo)$ ]] &&
  [[ "$P_MTU_MODE" =~ ^(auto|safe|maximum|custom)$ ]] && [[ "$P_SECURITY" =~ ^(plain|ipsec)$ ]] && [[ "$P_RESTART" =~ ^(off|1h|6h|12h|24h)$ ]] || return 1
  [ "$P_SECURITY" != ipsec ] || [[ "$P_IPSEC_PSK" =~ ^[0-9a-fA-F]{64}$ ]]
  [ "$P_GRE_KEY" = 0 ] || [[ "$P_GRE_KEY" =~ ^[0-9]+$ ]]
}

show_pair_code(){
  local name="$1" dir="$TUN_DIR/$1" code
  [ -r "$dir/meta.conf" ] || { bad "tunnel metadata missing"; return; }
  . "$dir/meta.conf"
  [ "$ROLE" = IRAN ] || { info "Pair Code is generated on the IRAN side"; return; }
  code="$(pair_code)"
  printf '%s\n' "$code" >"$dir/pair.code"
  chmod 600 "$dir/pair.code"
  echo; top; sect "PAIR CODE"
  row "$(printf '%spaste this on the KHAREJ server%s' "$D" "$N")"
  blank; bot
  echo; printf '%s%s%s\n' "$W" "$code" "$N"; echo
}

new_iran(){
  header "NEW TUNNEL - IRAN"
  top; sect "ROLE CHECK"; blank
  row "$(printf '%sIRAN creates the pair and exposes the user-facing ports.%s' "$D" "$N")"; row "$(printf '%sthe Pair Code is made here and pasted on KHAREJ.%s' "$D" "$N")"; bot; echo
  local detected oct dir id
  while :; do ask "tunnel name"; NAME="$ANS"; valid_name "$NAME" || { bad "letters, digits, - and _ only"; continue; }; [ -e "$TUN_DIR/$NAME" ] && { bad "name already exists"; continue; }; break; done
  dir="$TUN_DIR/$NAME"; detected="$(public_ipv4)"
  ask "iran public ip" "$detected"; LOCAL_PUBLIC="$ANS"; valid_ip4 "$LOCAL_PUBLIC" || { bad "invalid IPv4"; pause; return; }
  ask "kharej public ip"; REMOTE_PUBLIC="$ANS"; valid_ip4 "$REMOTE_PUBLIC" || { bad "invalid IPv4"; pause; return; }
  oct="$(next_pair)" || { bad "no free GRE subnet found"; pause; return; }; LOCAL_TUN="10.77.$oct.1"; REMOTE_TUN="10.77.$oct.2"; PREFIX=30
  GRE_KEY="$(gen_gre_key)"; choose_security || { pause; return; }; choose_profile; choose_mtu || { pause; return; }; RESTART_EVERY=off; pick_restart
  id="$(printf '%s' "$NAME" | sha256sum | cut -c1-8)"; IFNAME="$(iface_for "$NAME")"; ROLE=IRAN; ID="$id"; PEER_ID="$(peer_id_for "$LOCAL_PUBLIC" "$REMOTE_PUBLIC")"
  mkdir -p "$dir"; PARTIAL_TUNNEL="$NAME"; prompt_initial_ports "$NAME"
  save_meta "$dir" "NAME=$NAME" "ROLE=$ROLE" "ID=$ID" "IFNAME=$IFNAME" "LOCAL_PUBLIC=$LOCAL_PUBLIC" "REMOTE_PUBLIC=$REMOTE_PUBLIC" "LOCAL_TUN=$LOCAL_TUN" "REMOTE_TUN=$REMOTE_TUN" "PREFIX=$PREFIX" "PROFILE=$PROFILE" "MTU_MODE=$MTU_MODE" "PATH_MTU=$PATH_MTU" "MTU=$MTU" "TXQLEN=$TXQLEN" "GRE_KEY=$GRE_KEY" "SECURITY=$SECURITY" "IPSEC_PSK=$IPSEC_PSK" "PEER_ID=$PEER_ID" "PAIR_HASH=$(pair_shared_hash)" "RESTART_EVERY=$RESTART_EVERY"
  printf '%s\n' "$(pair_code)" >"$dir/pair.code"; chmod 600 "$dir/pair.code"; PARTIAL_TUNNEL=""
  [ "$SECURITY" = ipsec ] && security_sync_all
  echo; top; sect "CREATED - $NAME"; blank
  kv "role" "$W IRAN / pair owner$N"; kv "outer" "$W$LOCAL_PUBLIC -> $REMOTE_PUBLIC$N"; kv "inner" "$W$LOCAL_TUN/$PREFIX -> $REMOTE_TUN$N"; kv "security" "$W$SECURITY$N"; kv "mtu" "$W$MTU$N $D($MTU_MODE)$N"; kv "profile" "$W$PROFILE$N"; kv "restart" "$W$RESTART_EVERY$N"; bot; echo
  if service_start "$NAME"; then
    if ip link show "$IFNAME" >/dev/null 2>&1; then
      ok "GRE interface is up"
    elif [ "$SECURITY" = ipsec ]; then
      ok "Secure GRE armed - waiting for KHAREJ / IPsec peer"
      dim "watcher checks every 5s; service stays healthy while waiting"
    else
      warn "GRE armed but interface is not ready yet"
    fi
  else
    bad "service could not be armed - config and Pair Code were kept"
  fi
  set_restart_timer "$NAME" "$RESTART_EVERY"; show_pair_code "$NAME"
  [ "$SECURITY" = ipsec ] && warn "provider firewall must allow IKE/IPsec (UDP 500/4500 + ESP)" || warn "provider firewall/security-group must allow GRE protocol 47"
  pause
}

new_kharej(){
  header "NEW TUNNEL - KHAREJ"
  top; sect "ROLE CHECK"; blank
  row "$(printf '%sKHAREJ takes the Pair Code created on IRAN.%s' "$D" "$N")"; row "$(printf '%sGRE, MTU, security and restart settings come from the code.%s' "$D" "$N")"; bot; echo
  local code dir detected id oldpsk
  info "paste the pair code from the IRAN server"; ask "pair code"; code="$ANS"
  decode_pair "$code" || { bad "invalid or unsupported Pair Code"; dim "copy the complete DGR2-... code from IRAN"; pause; return; }
  echo; top; sect "PAIRED WITH"; blank
  kv "iran" "$W$P_IRAN_PUBLIC$N"; kv "kharej" "$W$P_KHAREJ_PUBLIC$N"; kv "inner" "$W$P_KHAREJ_TUN/$P_PREFIX -> $P_IRAN_TUN$N"; kv "security" "$W$P_SECURITY$N"; kv "mtu" "$W$P_MTU$N $D($P_MTU_MODE)$N"; kv "profile" "$W$P_PROFILE$N"; kv "restart" "$W$P_RESTART$N"; bot; echo
  NAME="$P_NAME"; dir="$TUN_DIR/$NAME"; [ ! -e "$dir" ] || { bad "tunnel already exists"; pause; return; }
  detected="$(public_ipv4)"; [ -n "$detected" ] && [ "$detected" != "$P_KHAREJ_PUBLIC" ] && warn "Pair Code expects $P_KHAREJ_PUBLIC but this server reports $detected"
  LOCAL_PUBLIC="$P_KHAREJ_PUBLIC"; REMOTE_PUBLIC="$P_IRAN_PUBLIC"; LOCAL_TUN="$P_KHAREJ_TUN"; REMOTE_TUN="$P_IRAN_TUN"; PREFIX="$P_PREFIX"
  PROFILE="$P_PROFILE"; MTU_MODE="$P_MTU_MODE"; PATH_MTU="$P_PATH_MTU"; MTU="$P_MTU"; TXQLEN="$P_TXQLEN"; GRE_KEY="$P_GRE_KEY"; SECURITY="$P_SECURITY"; IPSEC_PSK="$P_IPSEC_PSK"; RESTART_EVERY="$P_RESTART"
  if [ "$SECURITY" = ipsec ]; then ensure_ipsec_deps || { bad "strongSwan install failed"; pause; return; }; oldpsk="$(existing_peer_psk "$REMOTE_PUBLIC" 2>/dev/null || true)"; [ -z "$oldpsk" ] || [ "$oldpsk" = "$IPSEC_PSK" ] || { bad "this peer already uses a different IPsec key"; pause; return; }; fi
  id="$(printf '%s' "$NAME" | sha256sum | cut -c1-8)"; IFNAME="$(iface_for "$NAME")"; ROLE=KHAREJ; ID="$id"; PEER_ID="$(peer_id_for "$LOCAL_PUBLIC" "$REMOTE_PUBLIC")"
  save_meta "$dir" "NAME=$NAME" "ROLE=$ROLE" "ID=$ID" "IFNAME=$IFNAME" "LOCAL_PUBLIC=$LOCAL_PUBLIC" "REMOTE_PUBLIC=$REMOTE_PUBLIC" "LOCAL_TUN=$LOCAL_TUN" "REMOTE_TUN=$REMOTE_TUN" "PREFIX=$PREFIX" "PROFILE=$PROFILE" "MTU_MODE=$MTU_MODE" "PATH_MTU=$PATH_MTU" "MTU=$MTU" "TXQLEN=$TXQLEN" "GRE_KEY=$GRE_KEY" "SECURITY=$SECURITY" "IPSEC_PSK=$IPSEC_PSK" "PEER_ID=$PEER_ID" "PAIR_HASH=$(pair_shared_hash)" "RESTART_EVERY=$RESTART_EVERY"
  [ "$SECURITY" = ipsec ] && security_sync_all
  if service_start "$NAME"; then
    "$RUNNER" reconcile "$NAME" >/dev/null 2>&1 || true
    if ip link show "$IFNAME" >/dev/null 2>&1; then
      ok "GRE interface is up"
      ping -c 2 -W 2 "$REMOTE_TUN" >/dev/null 2>&1 && ok "inner peer responds: $REMOTE_TUN" || warn "GRE is up but inner peer is not responding yet"
    else
      ok "Secure GRE armed - waiting for IRAN / IPsec peer"
      dim "watcher checks every 5s; no tunnel deletion is needed"
    fi
  else
    bad "service could not be armed"
  fi
  set_restart_timer "$NAME" "$RESTART_EVERY"
  pause
}

list_tunnels(){
  local d n st role peer inner count=0
  printf '  %-18s %-8s %-9s %-16s %-18s\n' NAME ROLE STATE OUTER_PEER INNER_LINK
  printf '  %-18s %-8s %-9s %-16s %-18s\n' '------------------' '--------' '---------' '----------------' '------------------'
  shopt -s nullglob
  for d in "$TUN_DIR"/*; do
    [ -r "$d/meta.conf" ] || continue
    # shellcheck disable=SC1090
    . "$d/meta.conf"; n="$NAME"; role="$ROLE"; peer="$REMOTE_PUBLIC"; inner="$LOCAL_TUN->$REMOTE_TUN"; st="$(service_state "$n")"
    printf '  %-18s %-8s %-9s %-16s %-18s\n' "$n" "$role" "$st" "$peer" "$inner"; count=$((count+1))
  done
  shopt -u nullglob
  [ "$count" -gt 0 ] || echo "  no tunnels"
}

status_tunnel(){
  local n="$1" d="$TUN_DIR/$n"; . "$d/meta.conf"
  echo "  name       : $NAME"
  echo "  role       : $ROLE"
  echo "  service    : $(service_state "$NAME")"
  echo "  interface  : $IFNAME"
  echo "  outer      : $LOCAL_PUBLIC -> $REMOTE_PUBLIC"
  echo "  inner      : $LOCAL_TUN/$PREFIX -> $REMOTE_TUN"
  echo "  profile    : $PROFILE (mtu=$MTU txqlen=$TXQLEN)"
  if ip link show "$IFNAME" >/dev/null 2>&1; then
    ip -s link show "$IFNAME" | sed 's/^/    /'
  else warn "interface missing"; fi
  echo
  if [ "$ROLE" = IRAN ]; then
    echo "  forward ports:"
    if [ -s "$d/ports.list" ]; then sed 's/^/    /' "$d/ports.list"; else echo "    none"; fi
  fi
}

add_port_menu(){
  local n="$1" d="$TUN_DIR/$1" p proto target
  . "$d/meta.conf"; [ "$ROLE" = IRAN ] || { warn "forward ports are managed on IRAN"; return; }
  ask "Listen port on IRAN"; p="$ANS"; valid_port "$p" || { warn "invalid port"; return; }
  ask "Protocol tcp/udp/both" "tcp"; proto="${ANS,,}"
  ask "Target port on KHAREJ" "$p"; target="$ANS"; valid_port "$target" || { warn "invalid port"; return; }
  case "$proto" in tcp|udp) add_port_noninteractive "$n" "$proto" "$p" "$target" ;;
    both) add_port_noninteractive "$n" tcp "$p" "$target"; add_port_noninteractive "$n" udp "$p" "$target" ;;
    *) warn "invalid protocol"; return ;; esac
  ok "port map added - press Apply to activate"
}

remove_port_menu(){
  local n="$1" d="$TUN_DIR/$1" line tmp
  . "$d/meta.conf"; [ "$ROLE" = IRAN ] || { warn "forward ports are managed on IRAN"; return; }
  [ -s "$d/ports.list" ] || { warn "no ports"; return; }
  nl -ba "$d/ports.list" | sed 's/^/  /'
  ask "Line number to remove"; line="$ANS"; [[ "$line" =~ ^[0-9]+$ ]] || return
  tmp="$(mktemp)"; awk -v n="$line" 'NR!=n' "$d/ports.list" >"$tmp"; install -m 600 "$tmp" "$d/ports.list"; rm -f "$tmp"
  ok "removed - press Apply to activate"
}

delete_tunnel(){
  local n="$1" d="$TUN_DIR/$1" v
  read -r -p "  type the tunnel name to remove $n: " v; [ "$v" = "$n" ] || { warn "cancelled"; return; }
  systemctl disable "darkgre@$n.service" >/dev/null 2>&1 || true
  systemctl disable --now "darkgre-watch@$n.timer" >/dev/null 2>&1 || true
  systemctl disable --now "darkgre-watch@$n.service" >/dev/null 2>&1 || true
  set_restart_timer "$n" off; service_stop "$n"; rm -rf "$d"; security_sync_all
  ok "deleted $n"
}

pick_tunnel(){
  local names=() n i=1
  while read -r n; do [ -n "$n" ] && names+=("$n"); done < <(find "$TUN_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort)
  [ ${#names[@]} -gt 0 ] || { bad "no tunnels yet"; return 1; }
  echo; top; sect "TUNNELS"; blank
  for n in "${names[@]}"; do
    . "$TUN_DIR/$n/meta.conf"
    row "$(printf '%s[%d]%s %s %-13s %s%-6s%s %s%s%s' "$Y" "$i" "$N" "$(dot "$(service_state "$n")")" "$n" "$D" "$([ "$ROLE" = IRAN ] && echo iran || echo kharej)" "$N" "$D" "-> $REMOTE_PUBLIC" "$N")"
    i=$((i+1))
  done
  blank; item 0 "Back" ""; bot; echo; getkey
  [[ "$KEY" =~ ^[0-9]+$ ]] || return 1
  [ "$KEY" -gt 0 ] && [ "$KEY" -le ${#names[@]} ] || return 1
  SELECTED="${names[$((KEY-1))]}"
}

screen_ports(){
  local n="$1" d="$TUN_DIR/$1"
  while :; do
    . "$d/meta.conf"
    header "PORTS - $n"
    [ "$ROLE" = IRAN ] || { bad "ports are managed on the IRAN side only"; pause; return; }
    top; sect "USER PORTS"; blank
    if [ -s "$d/ports.list" ]; then
      local i=1 proto lp target
      while IFS=: read -r proto lp target; do [ -n "$proto" ] || continue
        row "$(printf '%s%2d.%s %s%-4s%s %s%-7s%s %s-> %s%s' "$D" "$i" "$N" "$C" "$proto" "$N" "$W" "$lp" "$N" "$D" "$target" "$N")"; i=$((i+1))
      done <"$d/ports.list"
    else row "$(printf '%s(none)%s' "$D" "$N")"; fi
    mid; item 1 "Add port" ""; item 2 "Remove port" "by row number"; item 3 "Apply + reload" ""; item 0 "Back" ""; bot; echo; getkey
    case "$KEY" in
      1) add_port_menu "$n"; pause ;;
      2) remove_port_menu "$n"; pause ;;
      3) "$RUNNER" reload-fw "$n" && ok "port rules applied" || bad "apply failed"; pause ;;
      0|_) return ;;
    esac
  done
}

screen_tuning(){
  local n="$1" d="$TUN_DIR/$1"
  while :; do
    . "$d/meta.conf"; SECURITY="${SECURITY:-plain}"; MTU_MODE="${MTU_MODE:-custom}"; PATH_MTU="${PATH_MTU:-0}"
    header "TUNING - $n"
    top; sect "CURRENT"; blank
    kv "profile" "$W$PROFILE$N"; kv "mtu" "$W$MTU$N $D($MTU_MODE / path $PATH_MTU)$N"; kv "txqueuelen" "$W$TXQLEN$N"
    mid; item 1 "Performance profile" ""; item 2 "MTU / PMTU" "auto scan, safe, max, custom"; item 3 "Rescan MTU" "DF probe now"; item 0 "Back" ""; bot; echo; getkey
    case "$KEY" in
      1) choose_profile; sed -i "s|^PROFILE=.*|PROFILE=$(printf %q "$PROFILE")|; s|^TXQLEN=.*|TXQLEN=$(printf %q "$TXQLEN")|" "$d/meta.conf" ;;
      2) choose_mtu || { pause; continue; }; sed -i "s|^MTU_MODE=.*|MTU_MODE=$(printf %q "$MTU_MODE")|; s|^PATH_MTU=.*|PATH_MTU=$(printf %q "$PATH_MTU")|; s|^MTU=.*|MTU=$(printf %q "$MTU")|" "$d/meta.conf" ;;
      3) PATH_MTU="$(scan_path_mtu "$REMOTE_PUBLIC" 2>/dev/null || echo 0)"
         [ "$PATH_MTU" -gt 0 ] || { bad "PMTU scan failed"; pause; continue; }
         MTU_MODE=auto; MTU="$(calc_inner_mtu "$PATH_MTU" "$SECURITY")"
         sed -i "s|^MTU_MODE=.*|MTU_MODE=auto|; s|^PATH_MTU=.*|PATH_MTU=$PATH_MTU|; s|^MTU=.*|MTU=$MTU|" "$d/meta.conf"; ok "Path MTU $PATH_MTU -> GRE MTU $MTU" ;;
      0|_) return ;;
      *) continue ;;
    esac
    service_restart "$n" >/dev/null 2>&1 || true
    . "$d/meta.conf"
    if [ "$ROLE" = IRAN ]; then warn "tuning changed - use the refreshed Pair Code on KHAREJ"; show_pair_code "$n"; else ok "tuning applied"; fi
    pause
  done
}

screen_endpoint(){
  local n="$1" d="$TUN_DIR/$1"
  . "$d/meta.conf"
  header "ENDPOINT - $n"
  top; sect "CURRENT"; blank
  kv "local public" "$W$LOCAL_PUBLIC$N"; kv "peer public" "$W$REMOTE_PUBLIC$N"; kv "inner local" "$W$LOCAL_TUN$N"; kv "inner peer" "$W$REMOTE_TUN$N"
  mid; item 1 "Change local IP" ""; item 2 "Change peer IP" ""; item 0 "Back" ""; bot; echo; getkey
  case "$KEY" in
    1) ask "new local public ip" "$LOCAL_PUBLIC"; valid_ip4 "$ANS" || { bad "invalid IPv4"; pause; return; }; LOCAL_PUBLIC="$ANS" ;;
    2) ask "new peer public ip" "$REMOTE_PUBLIC"; valid_ip4 "$ANS" || { bad "invalid IPv4"; pause; return; }; REMOTE_PUBLIC="$ANS" ;;
    *) return ;;
  esac
  PEER_ID="$(peer_id_for "$LOCAL_PUBLIC" "$REMOTE_PUBLIC")"
  sed -i "s|^LOCAL_PUBLIC=.*|LOCAL_PUBLIC=$(printf %q "$LOCAL_PUBLIC")|; s|^REMOTE_PUBLIC=.*|REMOTE_PUBLIC=$(printf %q "$REMOTE_PUBLIC")|; s|^PEER_ID=.*|PEER_ID=$(printf %q "$PEER_ID")|" "$d/meta.conf"
  security_sync_all; service_restart "$n" >/dev/null 2>&1 || true
  . "$d/meta.conf"
  if [ "$ROLE" = IRAN ]; then warn "endpoint changed - use the new Pair Code on KHAREJ"; show_pair_code "$n"; else ok "endpoint applied"; fi
  pause
}
screen_security(){
  local n="$1" d="$TUN_DIR/$1" old
  . "$d/meta.conf"; SECURITY="${SECURITY:-plain}"; IPSEC_PSK="${IPSEC_PSK:-}"
  header "SECURITY - $n"
  top; sect "CURRENT"; blank
  kv "mode" "$W$SECURITY$N"; [ "$SECURITY" = ipsec ] && kv "IPsec" "$W$(ipsec_state "$n")$N"
  mid
  if [ "$ROLE" = IRAN ]; then item 1 "GRE + IPsec" "AES-256-GCM / IKEv2"; item 2 "Plain GRE" "no encryption"; else row "$(printf '%ssecurity is controlled by the IRAN Pair Code%s' "$D" "$N")"; fi
  item 0 "Back" ""; bot; echo; getkey
  [ "$ROLE" = IRAN ] || return
  old="$SECURITY"
  case "$KEY" in
    1) SECURITY=ipsec; ensure_ipsec_deps || { bad "strongSwan install failed"; pause; return; }
       [ -n "$IPSEC_PSK" ] || IPSEC_PSK="$(existing_peer_psk "$REMOTE_PUBLIC" 2>/dev/null || true)"; [ -n "$IPSEC_PSK" ] || IPSEC_PSK="$(openssl rand -hex 32)" ;;
    2) SECURITY=plain; IPSEC_PSK="" ;;
    *) return ;;
  esac
  if [ "${MTU_MODE:-custom}" = auto ] || [ "$old" != "$SECURITY" ]; then
    PATH_MTU="$(scan_path_mtu "$REMOTE_PUBLIC" 2>/dev/null || echo 0)"
    [ "$PATH_MTU" -gt 0 ] && MTU="$(calc_inner_mtu "$PATH_MTU" "$SECURITY")" || { PATH_MTU=0; [ "$SECURITY" = ipsec ] && MTU=1360 || MTU=1400; }
    MTU_MODE=auto
  fi
  sed -i "s|^SECURITY=.*|SECURITY=$(printf %q "$SECURITY")|; s|^IPSEC_PSK=.*|IPSEC_PSK=$(printf %q "$IPSEC_PSK")|; s|^MTU_MODE=.*|MTU_MODE=$MTU_MODE|; s|^PATH_MTU=.*|PATH_MTU=$PATH_MTU|; s|^MTU=.*|MTU=$MTU|" "$d/meta.conf"
  security_sync_all; service_restart "$n" >/dev/null 2>&1 || true
  ok "security mode: $SECURITY"; warn "Pair Code changed - re-pair KHAREJ"; show_pair_code "$n"; pause
}
screen_restart(){
  local n="$1" d="$TUN_DIR/$1"; . "$d/meta.conf"; RESTART_EVERY="${RESTART_EVERY:-off}"
  header "SCHEDULED RESTART - $n"; kv "current" "$W$RESTART_EVERY$N"; pick_restart
  sed -i "s|^RESTART_EVERY=.*|RESTART_EVERY=$(printf %q "$RESTART_EVERY")|" "$d/meta.conf"; set_restart_timer "$n" "$RESTART_EVERY"; ok "scheduled restart: $RESTART_EVERY"; pause
}

screen_logs(){
  local n="$1" d="$TUN_DIR/$1"; . "$d/meta.conf"
  while :; do
    header "LOGS + INTERFACE - $n"
    top; sect "STATUS"; blank
    kv "state" "$W$(service_state "$n")$N"; kv "interface" "$W$IFNAME$N"; kv "inner peer" "$W$REMOTE_TUN$N"; kv "security" "$W$(ipsec_state "$n")$N"
    bot; echo
    ip -d link show "$IFNAME" 2>/dev/null | sed 's/^/    /' || true
    echo; top; item 1 "Last 60 lines" ""; item L "Live journal" ""; item x "XFRM / IPsec" ""; item 0 "Back" ""; bot; echo; getkey
    case "$KEY" in
      1) journalctl -u "darkgre@$n" -n 60 --no-pager -o cat 2>/dev/null | sed 's/^/    /'; pause ;;
      l|L) journalctl -u "darkgre@$n" -f -n 30 --no-pager ;;
      x|X) header "XFRM - $n"; ip xfrm state 2>/dev/null | sed 's/^/    /'; echo; ip xfrm policy 2>/dev/null | sed 's/^/    /'; pause ;;
      0|_) return ;;
    esac
  done
}
conn_rows(){
  local n="$1" d="$TUN_DIR/$1" lp proto line src sport
  . "$d/meta.conf"; command -v conntrack >/dev/null 2>&1 || return 0
  while IFS=: read -r proto lp _; do
    [ "$proto" = tcp ] || continue
    conntrack -L -p tcp 2>/dev/null | grep -E 'ESTABLISHED|ASSURED' | grep -E "dport=$lp([[:space:]]|$)" | head -n 30 |
    while IFS= read -r line; do
      src="$(sed -n 's/.*src=\([^ ]*\).*/\1/p' <<<"$line" | head -n1)"
      sport="$(sed -n 's/.*sport=\([^ ]*\).*/\1/p' <<<"$line" | head -n1)"
      [ -n "$src" ] && printf '%s\t%s\t%s\t%s\n' "$src" "$sport" "$lp" "$proto"
    done
  done <"$d/ports.list"
}
screen_connections(){
  local n="$1" d="$TUN_DIR/$1"
  . "$d/meta.conf"
  [ "$ROLE" = IRAN ] || { header "LIVE CONNECTIONS - $n"; bad "user connections land on the IRAN side"; pause; return; }
  command -v conntrack >/dev/null 2>&1 || { bad "conntrack is not installed"; pause; return; }
  while :; do
    header "LIVE CONNECTIONS - $n"
    top; sect "ESTABLISHED"; blank
    row "$(printf '%s%-22s %-8s %-8s %-5s%s' "$D" "client" "source" "port" "proto" "$N")"
    local total=0 src sport lp proto
    while IFS=$'\t' read -r src sport lp proto; do
      [ -n "$src" ] || continue; total=$((total+1))
      [ "$total" -le 18 ] && row "$(printf '%-22s %-8s %-8s %-5s' "${src:0:22}" "$sport" "$lp" "$proto")"
    done < <(conn_rows "$n")
    [ "$total" -eq 0 ] && row "$(printf '%sno user is connected right now%s' "$D" "$N")"
    mid; kv "clients" "$W$total$N"; kv "service" "$(dot "$(service_state "$n")") $(service_state "$n")  $D uptime $(svc_uptime_short "$n")$N"; bot
    printf '\n  %s2s refresh  -  any key to exit%s' "$D" "$N"
    read -rsn1 -t 2 _ && { echo; return; }
  done
}
speed_latency(){
  local n="$1" d="$TUN_DIR/$1" out
  . "$d/meta.conf"; header "LATENCY - $n"; info "10 pings through inner GRE link to $REMOTE_TUN"; echo
  out="$(ping -c 10 -W 2 "$REMOTE_TUN" 2>&1)"; printf '%s\n' "$out" | sed 's/^/    /'
  echo; top; sect "RESULT"; blank
  local loss avg
  loss="$(grep -oE '[0-9]+% packet loss' <<<"$out" | head -n1 | awk '{print $1}')"
  avg="$(grep -E '^(rtt|round-trip)' <<<"$out" | awk -F'=' '{print $2}' | awk -F'/' '{print $2}' | xargs)"
  kv "average" "$W${avg:--} ms$N"; kv "loss" "$W${loss:--}$N"; bot; pause
}
speed_passive(){
  local n="$1" win i1 o1 i2 o2 din dout
  header "PASSIVE THROUGHPUT - $n"; read -r i1 o1 <<<"$(tunnel_traffic "$n")"
  ask "sample for how many seconds" "10"; win="${ANS:-10}"; [[ "$win" =~ ^[0-9]+$ ]] && [ "$win" -ge 2 ] || win=10
  info "sampling GRE interface for ${win}s"; sleep "$win"; read -r i2 o2 <<<"$(tunnel_traffic "$n")"
  din=$(( (i2-i1)/win )); dout=$(( (o2-o1)/win )); [ "$din" -lt 0 ] && din=0; [ "$dout" -lt 0 ] && dout=0
  echo; top; sect "RESULT"; blank
  kv "receive" "$W$(human_bytes "$din")/s$N  $D$((din*8/1000000)) Mbps$N"; kv "transmit" "$W$(human_bytes "$dout")/s$N  $D$((dout*8/1000000)) Mbps$N"
  kv "rx total" "$W$(human_bytes "$i2")$N"; kv "tx total" "$W$(human_bytes "$o2")$N"; bot; pause
}
speed_responder(){
  pick_tunnel || return; local n="$SELECTED" d="$TUN_DIR/$SELECTED"; . "$d/meta.conf"
  [ "$ROLE" = KHAREJ ] || { bad "run responder on KHAREJ"; pause; return; }
  install_iperf3 || { bad "iperf3 install failed"; pause; return; }
  ask "responder port" "19999"; valid_port "$ANS" || { bad "invalid port"; pause; return; }
  header "SPEED RESPONDER - $n"; info "waiting on $LOCAL_TUN:$ANS - ctrl+c to stop"; echo
  iperf3 -s -1 -B "$LOCAL_TUN" -p "$ANS"; pause
}
speed_active(){
  local n="$1" d="$TUN_DIR/$1" secs port
  . "$d/meta.conf"; [ "$ROLE" = IRAN ] || { bad "run active test on IRAN"; pause; return; }
  install_iperf3 || { bad "iperf3 install failed"; pause; return; }
  header "ACTIVE THROUGHPUT - $n"
  row "$(printf '%sstart Diagnostics -> Speed responder on KHAREJ first%s' "$Y" "$N")"; echo
  yesno "responder is running?" n || return
  ask "responder port" "19999"; port="$ANS"; valid_port "$port" || { bad "invalid port"; pause; return; }
  ask "seconds" "10"; secs="$ANS"; [[ "$secs" =~ ^[0-9]+$ ]] || secs=10
  iperf3 -c "$REMOTE_TUN" -p "$port" -t "$secs"; pause
}
speed_screen(){
  local n="$1"
  while :; do
    header "SPEED - $n"; top; sect "OPTIONS"; blank
    item 1 "Latency" "inner GRE RTT"; item 2 "Passive throughput" "real interface traffic"; item 3 "Active throughput" "iperf3 benchmark"; item 0 "Back" ""; bot; echo; getkey
    case "$KEY" in 1) speed_latency "$n";; 2) speed_passive "$n";; 3) speed_active "$n";; 0|_) return;; esac
  done
}
screen_fingerprint(){
  header "CONFIG FINGERPRINT"; top; sect "MUST MATCH"; blank
  row "$(printf '%scompare the same tunnel on IRAN and KHAREJ%s' "$D" "$N")"; row "$(printf '%sIPsec secret is hashed and never printed%s' "$D" "$N")"; bot; echo
  local d
  shopt -s nullglob
  for d in "$TUN_DIR"/*; do [ -r "$d/meta.conf" ] || continue; . "$d/meta.conf"; printf '  %s%-16s%s %s(%s)%s  %s%s%s\n' "$W" "$NAME" "$N" "$D" "$ROLE" "$N" "$C" "$(config_fingerprint "$NAME")" "$N"; done
  shopt -u nullglob; pause
}


apply_pair_code_existing(){
  local n="$1" d="$TUN_DIR/$1" code oldpsk
  . "$d/meta.conf"
  [ "$ROLE" = KHAREJ ] || { info "IRAN owns the Pair Code"; return; }
  header "RE-PAIR - $n"; info "paste the refreshed Pair Code from IRAN"; ask "pair code"; code="$ANS"
  decode_pair "$code" || { bad "invalid Pair Code"; pause; return; }
  [ "$P_NAME" = "$n" ] || { bad "Pair Code belongs to tunnel $P_NAME, not $n"; pause; return; }
  LOCAL_PUBLIC="$P_KHAREJ_PUBLIC"; REMOTE_PUBLIC="$P_IRAN_PUBLIC"; LOCAL_TUN="$P_KHAREJ_TUN"; REMOTE_TUN="$P_IRAN_TUN"; PREFIX="$P_PREFIX"
  PROFILE="$P_PROFILE"; MTU_MODE="$P_MTU_MODE"; PATH_MTU="$P_PATH_MTU"; MTU="$P_MTU"; TXQLEN="$P_TXQLEN"; GRE_KEY="$P_GRE_KEY"; SECURITY="$P_SECURITY"; IPSEC_PSK="$P_IPSEC_PSK"; RESTART_EVERY="$P_RESTART"
  if [ "$SECURITY" = ipsec ]; then
    ensure_ipsec_deps || { bad "strongSwan install failed"; pause; return; }
    oldpsk="$(existing_peer_psk "$REMOTE_PUBLIC" 2>/dev/null || true)"
    [ -z "$oldpsk" ] || [ "$oldpsk" = "$IPSEC_PSK" ] || warn "replacing the shared IPsec key for this peer"
  fi
  PEER_ID="$(peer_id_for "$LOCAL_PUBLIC" "$REMOTE_PUBLIC")"
  save_meta "$d" "NAME=$n" "ROLE=KHAREJ" "ID=$ID" "IFNAME=$IFNAME" "LOCAL_PUBLIC=$LOCAL_PUBLIC" "REMOTE_PUBLIC=$REMOTE_PUBLIC" "LOCAL_TUN=$LOCAL_TUN" "REMOTE_TUN=$REMOTE_TUN" "PREFIX=$PREFIX" "PROFILE=$PROFILE" "MTU_MODE=$MTU_MODE" "PATH_MTU=$PATH_MTU" "MTU=$MTU" "TXQLEN=$TXQLEN" "GRE_KEY=$GRE_KEY" "SECURITY=$SECURITY" "IPSEC_PSK=$IPSEC_PSK" "PEER_ID=$PEER_ID" "PAIR_HASH=$(pair_shared_hash)" "RESTART_EVERY=$RESTART_EVERY"
  security_sync_all; set_restart_timer "$n" "$RESTART_EVERY"
  service_restart "$n" >/dev/null 2>&1 && ok "Pair Code applied and tunnel restarted" || bad "re-pair saved but service did not start"
  pause
}

manage(){
  pick_tunnel || return
  local n="$SELECTED" d="$TUN_DIR/$SELECTED"
  while :; do
    [ -r "$d/meta.conf" ] || return; . "$d/meta.conf"
    SECURITY="${SECURITY:-plain}"; MTU_MODE="${MTU_MODE:-custom}"; RESTART_EVERY="${RESTART_EVERY:-off}"
    local rx tx; read -r rx tx <<<"$(tunnel_traffic "$n")"
    header "TUNNEL - $n"
    top; sect "STATUS"; blank
    kv "state" "$(case "$(service_state "$n")" in active) badge ACTIVE "$BG_OK$W";; failed) badge FAILED "$BG_ERR$W";; *) badge INACTIVE "$BG_WARN$W";; esac)  $D uptime $(svc_uptime_short "$n")$N"
    kv "role" "$W$([ "$ROLE" = IRAN ] && echo 'IRAN (pair owner)' || echo 'KHAREJ (peer)')$N"
    kv "outer" "$W$LOCAL_PUBLIC -> $REMOTE_PUBLIC$N"; kv "inner" "$W$LOCAL_TUN/$PREFIX -> $REMOTE_TUN$N"
    kv "security" "$W$SECURITY$N $D$(ipsec_state "$n")$N"; kv "profile" "$W$PROFILE$N"; kv "mtu" "$W$MTU$N $D$MTU_MODE$N"
    kv "traffic" "$L4$(human_bytes "$rx") rx$N  $L6$(human_bytes "$tx") tx$N"; kv "restart" "$W$RESTART_EVERY$N"
    if [ -n "${PAIR_HASH:-}" ]; then kv "pair hash" "$W$PAIR_HASH$N"; else kv "pair hash" "$Y legacy / re-pair recommended$N"; fi
    mid; sect "CONTROL"; item 1 "Start" ""; item 2 "Stop" ""; item 3 "Restart" ""
    mid; sect "PAIRING"
    if [ "$ROLE" = IRAN ]; then item p "Pair code" "paste this on KHAREJ"; else item p "Apply Pair Code" "re-pair without deleting"; fi
    mid; sect "CONFIGURE"; [ "$ROLE" = IRAN ] && item 4 "Ports" "user-facing ports"; item 5 "Tuning" "profile + MTU / PMTU"; item 6 "Endpoint" "local / peer IP"; item 7 "Security" "GRE + IPsec"; item 8 "Scheduled restart" ""
    mid; sect "INSPECT"; item s "Speed test" "latency + throughput"; [ "$ROLE" = IRAN ] && item c "Live connections" ""; item L "Logs + interface" ""; item f "Config fingerprint" ""; item v "Show config" ""
    mid; sect "ADVANCED"; item e "Edit metadata" "advanced / manual"; item d "Delete tunnel" ""; item 0 "Back" ""; bot; echo; getkey
    case "$KEY" in
      1) service_start "$n"; pause ;; 2) service_stop "$n"; pause ;; 3) service_restart "$n"; pause ;;
      4) [ "$ROLE" = IRAN ] && screen_ports "$n" || { info "ports are managed on IRAN"; pause; } ;;
      5) screen_tuning "$n" ;; 6) screen_endpoint "$n" ;; 7) screen_security "$n" ;; 8) screen_restart "$n" ;;
      p|P) if [ "$ROLE" = IRAN ]; then show_pair_code "$n"; pause; else apply_pair_code_existing "$n"; fi ;;
      s|S) speed_screen "$n" ;; c|C) [ "$ROLE" = IRAN ] && screen_connections "$n" || { info "connections are visible on IRAN"; pause; } ;;
      l|L) screen_logs "$n" ;; f|F) header "FINGERPRINT - $n"; kv "fingerprint" "$W$(config_fingerprint "$n")$N"; pause ;;
      v|V) header "CONFIG - $n"; sed 's/^/    /' "$d/meta.conf"; echo; [ -s "$d/ports.list" ] && { info "ports"; sed 's/^/    /' "$d/ports.list"; }; pause ;;
      e|E) local ed=nano; command -v nano >/dev/null 2>&1 || ed=vi; "$ed" "$d/meta.conf"; security_sync_all; service_restart "$n" >/dev/null 2>&1 || true; warn "manual metadata edits can break pairing"; pause ;;
      d|D) delete_tunnel "$n"; pause; [ -d "$d" ] || return ;;
      0|_) return ;;
    esac
  done
}

dashboard(){
  [ -d "$TUN_DIR" ] || return
  while :; do
    header "LIVE DASHBOARD"
    top; sect "TUNNELS"; row "$(printf '%s%-11s %-6s %-9s %-8s %-8s %-9s %-8s%s' "$D" "name" "role" "state" "mtu" "security" "latency" "traffic" "$N")"; blank
    local d st lat rx tx sec
    shopt -s nullglob
    for d in "$TUN_DIR"/*; do
      [ -r "$d/meta.conf" ] || continue; . "$d/meta.conf"; st="$(service_state "$NAME")"; sec="$(ipsec_state "$NAME")"; read -r rx tx <<<"$(tunnel_traffic "$NAME")"
      lat="$(ping -c1 -W1 "$REMOTE_TUN" 2>/dev/null | sed -n 's/.*time=\([0-9.]*\).*/\1/p' | head -n1)"; [ -n "$lat" ] || lat="-"
      row "$(printf '%s %-11s %-6s %-9s %-8s %-8s %-9s %-8s' "$(dot "$st")" "${NAME:0:11}" "$ROLE" "$st" "$MTU" "${sec:0:8}" "$lat" "$(human_bytes $((rx+tx)))")"
    done
    shopt -u nullglob
    bot; printf '\n  %s2s refresh  -  any key to exit%s' "$D" "$N"; read -rsn1 -t 2 _ && { echo; return; }
  done
}

screen_core(){
  while :; do
    header "GRE CORE"
    top; sect "STATUS"; blank
    command -v ip >/dev/null 2>&1 && kv "iproute2" "$G ready$N" || kv "iproute2" "$R missing$N"
    command -v iptables >/dev/null 2>&1 && kv "iptables" "$G ready$N" || kv "iptables" "$R missing$N"
    [ -d /sys/module/ip_gre ] && kv "ip_gre" "$G loaded$N" || kv "ip_gre" "$Y not loaded$N"
    command -v conntrack >/dev/null 2>&1 && kv "conntrack" "$G ready$N" || kv "conntrack" "$Y optional$N"
    command -v ipsec >/dev/null 2>&1 && kv "strongSwan" "$G ready$N" || kv "strongSwan" "$D not installed$N"
    kv "forwarding" "$W$(sysctl -n net.ipv4.ip_forward 2>/dev/null || echo '?')$N"
    mid; item 1 "Install / repair" "GRE dependencies"; item 2 "Load GRE module" ""; item 3 "Security core" "install strongSwan"; item 4 "iperf3" "speed-test core"; item 0 "Back" ""; bot; echo; getkey
    case "$KEY" in
      1) ensure_deps; ensure_system; ok "GRE core checked"; pause ;;
      2) modprobe ip_gre >/dev/null 2>&1 && ok "ip_gre loaded" || bad "could not load ip_gre"; pause ;;
      3) ensure_ipsec_deps && { security_sync_all; ok "strongSwan ready"; } || bad "security core install failed"; pause ;;
      4) install_iperf3 && ok "iperf3 ready" || bad "iperf3 install failed"; pause ;;
      0|_) return ;;
    esac
  done
}

diagnostics(){
  while :; do
    header "DIAGNOSTICS"
    top; sect "LOGS"; blank; item 1 "Live log" ""; item 2 "Last 60 lines" ""
    mid; sect "TESTS"; item 3 "Health check" "all tunnels"; item 4 "Link test" "inner peer + MTU"; item 5 "Config fingerprint" "compare both servers"; item 6 "Path MTU scan" "outer DF probe"; item 7 "Pair integrity" "GRE key / stale Pair detection"; item r "Speed responder" "run on KHAREJ"
    mid; sect "SECURITY"; item i "IPsec / XFRM status" ""; item 0 "Back" ""; bot; echo; getkey
    case "$KEY" in
      1) pick_tunnel && journalctl -u "darkgre@$SELECTED" -f -n 30 --no-pager; pause ;;
      2) pick_tunnel && { header "LOG - $SELECTED"; journalctl -u "darkgre@$SELECTED" -n 60 --no-pager -o cat | sed 's/^/    /'; }; pause ;;
      3) health_check; pause ;;
      4) pick_tunnel || continue; . "$TUN_DIR/$SELECTED/meta.conf"; header "LINK - $SELECTED"; ping -c 5 -W 2 "$REMOTE_TUN" || true; echo; ip -d link show "$IFNAME" | sed 's/^/    /'; pause ;;
      5) screen_fingerprint ;;
      6) pick_tunnel || continue; . "$TUN_DIR/$SELECTED/meta.conf"; header "PMTU - $SELECTED"; info "scanning $REMOTE_PUBLIC"; local p; p="$(scan_path_mtu "$REMOTE_PUBLIC" 2>/dev/null || echo 0)"; [ "$p" -gt 0 ] && { ok "Path MTU: $p"; ok "recommended GRE MTU: $(calc_inner_mtu "$p" "${SECURITY:-plain}")"; } || bad "scan failed"; pause ;;
      7) pick_tunnel && { header "PAIR INTEGRITY - $SELECTED"; pair_integrity_check "$SELECTED"; pause; } ;;
      r|R) speed_responder ;;
      i|I) header "IPSEC / XFRM"; command -v ipsec >/dev/null 2>&1 && ipsec statusall | sed 's/^/    /' || dim "strongSwan not installed"; echo; ip xfrm state 2>/dev/null | sed 's/^/    /'; pause ;;
      0|_) return ;;
    esac
  done
}

update_self(){
  local url tmp ver
  url="$(cat "$UPDATE_URL_FILE" 2>/dev/null)"
  [ -n "$url" ] || { bad "no update URL configured"; return; }
  tmp="$(mktemp)"; info "downloading"
  curl -fsSL --retry 3 --max-time 60 -o "$tmp" "$url" || { bad "download failed"; rm -f "$tmp"; return; }
  grep -q 'DARKVPN-GRE-SCRIPT' "$tmp" && bash -n "$tmp" || { bad "invalid update"; rm -f "$tmp"; return; }
  ver="$(grep -m1 '^SCRIPT_VER=' "$tmp" | cut -d'"' -f2)"; cp -f "$SELF_PATH" "$SELF_PATH.bak" 2>/dev/null || true
  install -m 0755 "$tmp" "$SELF_PATH"; rm -f "$tmp"
  if DARK_GRE_REPAIR_ONLY=1 "$SELF_PATH"; then
    ok "updated to v${ver:-?} and runtime repaired"
  else
    bad "script updated, but runtime repair failed"
    return 1
  fi
}
screen_update(){
  while :; do
    header "UPDATE"; top; sect "VERSIONS"; blank
    kv "core" "$WLinux GRE$N"; kv "security" "$W$(command -v ipsec >/dev/null 2>&1 && echo strongSwan || echo optional)$N"; kv "script" "$Wv$SCRIPT_VER$N"; kv "source" "$D$(cat "$UPDATE_URL_FILE" 2>/dev/null || echo 'not set')$N"
    mid; item 1 "Core" "install / repair"; item 2 "Update script" "from source url"; item 3 "Set source url" ""; item 4 "Install as command" "run as: darkgre"; item 0 "Back" ""; bot; echo; getkey
    case "$KEY" in
      1) screen_core ;;
      2) update_self; pause ;;
      3) ask "raw url"; [ -n "$ANS" ] && { echo "$ANS" >"$UPDATE_URL_FILE"; ok "saved"; }; pause ;;
      4) install -m 0755 "$SELF_PATH" /usr/local/bin/darkgre && ok "run: darkgre" || bad "failed"; pause ;;
      0|_) return ;;
    esac
  done
}

uninstall_all(){
  header "UNINSTALL"; warn "removes every DARK GRE tunnel and service"; echo; ask "type UNINSTALL to confirm"
  [ "$ANS" = UNINSTALL ] || { info "cancelled"; pause; return; }
  local d; shopt -s nullglob
  for d in "$TUN_DIR"/*; do [ -r "$d/meta.conf" ] || continue; . "$d/meta.conf"; systemctl disable "darkgre@$NAME.service" >/dev/null 2>&1 || true; set_restart_timer "$NAME" off; service_stop "$NAME"; done
  shopt -u nullglob
  rm -f "$UNIT_FILE" "$RS_UNIT" "$RS_TIMER" "$WATCH_UNIT" "$WATCH_TIMER" "$RUNNER" /etc/sysctl.d/99-dark-gre.conf
  sed -i '\|include /etc/dark-gre/security/ipsec.d/\*.conf|d;\|include /etc/ipsec.d/dark-gre/\*.conf|d' /etc/ipsec.conf 2>/dev/null || true
  sed -i '\|include /etc/dark-gre/security/ipsec.secrets|d;\|include /etc/ipsec.dark-gre.secrets|d' /etc/ipsec.secrets 2>/dev/null || true
  rm -rf "$IPSEC_DIR" "$IPSEC_SECRETS" "$BASE_DIR"; systemctl daemon-reload; command -v ipsec >/dev/null 2>&1 && ipsec reload >/dev/null 2>&1 || true
  ok "DARK GRE uninstalled; strongSwan package was left installed"; pause; exit 0
}

repair_runtime(){
  need_root
  ensure_deps
  ensure_system
  migrate_existing_tunnels

  local d n
  # Phase 1: stop DARK GRE services/watchers before changing strongSwan state.
  shopt -s nullglob
  for d in "$TUN_DIR"/*; do
    [ -r "$d/meta.conf" ] || continue
    . "$d/meta.conf"; n="$NAME"
    systemctl disable --now "darkgre-watch@$n.timer" >/dev/null 2>&1 || true
    systemctl disable --now "darkgre-watch@$n.service" >/dev/null 2>&1 || true
    systemctl stop "darkgre@$n.service" >/dev/null 2>&1 || true
    systemctl reset-failed "darkgre@$n.service" >/dev/null 2>&1 || true
  done
  shopt -u nullglob

  # Phase 2: load the final IPsec config once. IRAN auto=start owns initiation.
  security_sync_all
  sleep 1

  # Phase 3: arm GRE and policy watcher. Never tear IPsec down after sync.
  shopt -s nullglob
  for d in "$TUN_DIR"/*; do
    [ -r "$d/meta.conf" ] || continue
    . "$d/meta.conf"; n="$NAME"
    systemctl enable "darkgre@$n.service" >/dev/null 2>&1 || true
    systemctl start "darkgre@$n.service" >/dev/null 2>&1 || true
    systemctl enable --now "darkgre-watch@$n.service" >/dev/null 2>&1 || true
    "$RUNNER" reconcile "$n" >/dev/null 2>&1 || true
    RESTART_EVERY="${RESTART_EVERY:-off}"
    set_restart_timer "$n" "$RESTART_EVERY"
  done
  shopt -u nullglob

  systemctl daemon-reload >/dev/null 2>&1 || true
  return 0
}

main(){
  need_root; ensure_deps; ensure_system; migrate_existing_tunnels; sweep_partials
  trap on_interrupt INT TERM
  security_sync_all
  while :; do
    header
    local tot run; tot="$(find "$TUN_DIR" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)"; run="$(systemctl list-units 'darkgre@*' --state=active --no-legend 2>/dev/null | grep -c .)"
    top; row "$(printf '%s%s%s tunnels   %s%s%s running   %s%s%s' "$W$BD" "$tot" "$N" "$G$BD" "$run" "$N" "$D" "$(gre_core_ready && echo 'core ready' || echo 'core check')" "$N")"
    mid; sect "SETUP"; item 1 "Core" "GRE + security cores"; item 2 "New tunnel - IRAN" "makes Pair Code"; item 3 "New tunnel - KHAREJ" "takes Pair Code"
    mid; sect "OPERATE"; item 4 "Manage tunnels" "ports, security, MTU, endpoint"; item 5 "Dashboard" ""; item 6 "Diagnostics" "logs, tests, fingerprint"
    mid; sect "MAINTENANCE"; item 7 "Update" ""; item 8 "Uninstall" ""; item 0 "Exit" ""; bot; echo; getkey
    case "$KEY" in
      1) screen_core ;; 2) new_iran ;; 3) new_kharej ;; 4) manage ;; 5) dashboard ;; 6) diagnostics ;; 7) screen_update ;; 8) uninstall_all ;;
      0|q|Q) clear; printf '  %sDARK VPN - GRE Direct%s  %s%s%s\n\n' "$C" "$N" "$D" "$DEV_ID" "$N"; exit 0 ;;
    esac
  done
}

if [ "${DARK_GRE_REPAIR_ONLY:-0}" = 1 ]; then
  repair_runtime
  exit $?
fi

if [ "${DARK_GRE_LIB_ONLY:-0}" != 1 ]; then
  main "$@"
fi
