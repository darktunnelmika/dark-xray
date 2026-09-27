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

SCRIPT_VER="0.1.0-rc1"
DEV_ID="@mikakhadm"
BASE_DIR="/etc/dark-gre"
TUN_DIR="$BASE_DIR/tunnels"
RUNNER="/usr/local/libexec/darkgre-runner"
UNIT_FILE="/etc/systemd/system/darkgre@.service"
UPDATE_URL_FILE="$BASE_DIR/update.url"
SELF_PATH="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "$0")"

if [ "${DARK_GRE_LIB_ONLY:-0}" != 1 ] && [ ! -t 0 ] && [ -r /dev/tty ]; then
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

primary_ipv4(){
  local dev addr
  dev="$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); exit}}')"
  [ -n "$dev" ] && addr="$(ip -4 -o addr show dev "$dev" scope global 2>/dev/null | awk '{split($4,a,"/"); print a[1]; exit}')"
  printf '%s\n' "${addr:-}"
}
public_ipv4(){
  local v; v="$(curl -4 -fsS --max-time 4 https://api.ipify.org 2>/dev/null || true)"
  valid_ip4 "$v" && { echo "$v"; return; }
  primary_ipv4
}

ensure_deps(){
  local missing=() c
  for c in ip iptables systemctl base64 sha256sum awk sed grep curl; do command -v "$c" >/dev/null 2>&1 || missing+=("$c"); done
  [ "${#missing[@]}" -eq 0 ] && return 0
  info "installing dependencies"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iproute2 iptables kmod coreutils curl >/dev/null
  else
    bad "automatic dependency installation currently requires apt"; exit 1
  fi
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
  case "$1" in
    stable)   MTU=1380; TXQLEN=1000 ;;
    lowping)  MTU=1400; TXQLEN=500  ;;
    turbo)    MTU=1476; TXQLEN=2000 ;;
    *)        PROFILE=balanced; MTU=1436; TXQLEN=1000 ;;
  esac
}

write_runner(){
  install -d -m 0755 "$(dirname "$RUNNER")"
  cat >"$RUNNER" <<'RUNNER_EOF'
#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="/etc/dark-gre"
TUN_DIR="$BASE_DIR/tunnels"
action="${1:-up}"
name="${2:-}"
[ -n "$name" ] || { echo "missing tunnel name" >&2; exit 2; }
conf="$TUN_DIR/$name/meta.conf"
[ -r "$conf" ] || { echo "missing $conf" >&2; exit 3; }
# shellcheck disable=SC1090
. "$conf"

nat_chain="DGRN_${ID}"
post_chain="DGRP_${ID}"
fw_chain="DGRF_${ID}"

remove_chain(){
  local table="$1" chain="$2" hook="$3"
  iptables -t "$table" -D "$hook" -j "$chain" 2>/dev/null || true
  iptables -t "$table" -F "$chain" 2>/dev/null || true
  iptables -t "$table" -X "$chain" 2>/dev/null || true
}
remove_fw(){
  remove_chain nat "$nat_chain" PREROUTING
  remove_chain nat "$post_chain" POSTROUTING
  remove_chain filter "$fw_chain" FORWARD
}
apply_fw(){
  [ "$ROLE" = "IRAN" ] || return 0
  remove_fw
  iptables -t nat -N "$nat_chain"
  iptables -t nat -N "$post_chain"
  iptables -t filter -N "$fw_chain"
  iptables -t nat -I PREROUTING 1 -j "$nat_chain"
  iptables -t nat -I POSTROUTING 1 -j "$post_chain"
  iptables -t filter -I FORWARD 1 -j "$fw_chain"
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

case "$action" in
  up)
    modprobe ip_gre 2>/dev/null || true
    ip tunnel del "$IFNAME" 2>/dev/null || true
    ip tunnel add "$IFNAME" mode gre local "$LOCAL_PUBLIC" remote "$REMOTE_PUBLIC" ttl 64
    ip addr add "$LOCAL_TUN/$PREFIX" dev "$IFNAME"
    ip link set dev "$IFNAME" mtu "$MTU" txqueuelen "$TXQLEN" up
    sysctl -q -w net.ipv4.ip_forward=1 >/dev/null
    apply_fw
    ;;
  down)
    remove_fw
    ip link set dev "$IFNAME" down 2>/dev/null || true
    ip tunnel del "$IFNAME" 2>/dev/null || true
    ;;
  reload-fw)
    apply_fw
    ;;
  *) echo "unknown action: $action" >&2; exit 4 ;;
esac
RUNNER_EOF
  chmod 0755 "$RUNNER"
}

write_unit(){
  cat >"$UNIT_FILE" <<UNIT_EOF
[Unit]
Description=DARK GRE Direct tunnel %i
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$RUNNER up %i
ExecStop=$RUNNER down %i
TimeoutStartSec=20
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
UNIT_EOF
  systemctl daemon-reload
}

ensure_system(){
  mkdir -p "$TUN_DIR" "$BASE_DIR"
  chmod 700 "$BASE_DIR" "$TUN_DIR"
  write_runner
  write_unit
  cat >/etc/sysctl.d/99-dark-gre.conf <<'SYSCTL_EOF'
net.ipv4.ip_forward=1
SYSCTL_EOF
  sysctl -q --system >/dev/null 2>&1 || true
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

service_start(){ systemctl enable --now "darkgre@$1.service" >/dev/null; }
service_restart(){ systemctl restart "darkgre@$1.service"; }
service_stop(){ systemctl stop "darkgre@$1.service" 2>/dev/null || true; }
service_state(){ systemctl is-active "darkgre@$1.service" 2>/dev/null || echo inactive; }

choose_profile(){
  echo; top; sect "PROFILE"; blank
  item 1 "Balanced" "MTU 1436 - recommended"
  item 2 "Stable" "MTU 1380 - safer"
  item 3 "Low Ping" "MTU 1400 - interactive"
  item 4 "Turbo" "MTU 1476 - clean paths"
  bot; echo; getkey
  case "$KEY" in
    2) PROFILE=stable ;;
    3) PROFILE=lowping ;;
    4) PROFILE=turbo ;;
    *) PROFILE=balanced ;;
  esac
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
  payload="1|$NAME|$LOCAL_PUBLIC|$REMOTE_PUBLIC|$LOCAL_TUN|$REMOTE_TUN|$PREFIX|$PROFILE|$MTU|$TXQLEN"
  sum="$(sha12 "$payload")"
  printf 'DGR1-%s-%s\n' "$sum" "$(printf '%s' "$payload" | b64enc)"
}

decode_pair(){
  local code="$1" sum enc payload calc ver
  [[ "$code" =~ ^DGR1-([0-9a-f]{12})-(.+)$ ]] || return 1
  sum="${BASH_REMATCH[1]}"; enc="${BASH_REMATCH[2]}"
  payload="$(printf '%s' "$enc" | b64dec)" || return 1
  calc="$(sha12 "$payload")"; [ "$calc" = "$sum" ] || return 1
  IFS='|' read -r ver P_NAME P_IRAN_PUBLIC P_KHAREJ_PUBLIC P_IRAN_TUN P_KHAREJ_TUN P_PREFIX P_PROFILE P_MTU P_TXQLEN <<<"$payload"
  [ "$ver" = 1 ] &&
    valid_name "$P_NAME" &&
    valid_ip4 "$P_IRAN_PUBLIC" &&
    valid_ip4 "$P_KHAREJ_PUBLIC" &&
    valid_ip4 "$P_IRAN_TUN" &&
    valid_ip4 "$P_KHAREJ_TUN" &&
    [[ "$P_PREFIX" =~ ^[0-9]+$ ]] && [ "$P_PREFIX" -ge 8 ] && [ "$P_PREFIX" -le 32 ] &&
    [[ "$P_MTU" =~ ^[0-9]+$ ]] && [ "$P_MTU" -ge 576 ] && [ "$P_MTU" -le 1476 ] &&
    [[ "$P_TXQLEN" =~ ^[0-9]+$ ]] && [ "$P_TXQLEN" -ge 100 ] && [ "$P_TXQLEN" -le 10000 ] &&
    [[ "$P_PROFILE" =~ ^(balanced|stable|lowping|turbo)$ ]]
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
  row "$(printf '%sIRAN creates the pair and exposes the user-facing ports.%s' "$D" "$N")"
  row "$(printf '%sthe Pair Code is made here and pasted on KHAREJ.%s' "$D" "$N")"
  bot; echo
  local detected oct dir id
  while :; do
    ask "tunnel name"
    NAME="$ANS"
    valid_name "$NAME" || { bad "letters, digits, - and _ only"; continue; }
    [ -e "$TUN_DIR/$NAME" ] && { bad "name already exists"; continue; }
    break
  done
  dir="$TUN_DIR/$NAME"
  detected="$(public_ipv4)"
  ask "iran public ip" "$detected"; LOCAL_PUBLIC="$ANS"
  valid_ip4 "$LOCAL_PUBLIC" || { bad "invalid IPv4"; pause; return; }
  ask "kharej public ip"; REMOTE_PUBLIC="$ANS"
  valid_ip4 "$REMOTE_PUBLIC" || { bad "invalid IPv4"; pause; return; }
  oct="$(next_pair)" || { bad "no free GRE subnet found"; pause; return; }
  LOCAL_TUN="10.77.$oct.1"; REMOTE_TUN="10.77.$oct.2"; PREFIX=30
  choose_profile
  id="$(printf '%s' "$NAME" | sha256sum | cut -c1-8)"
  IFNAME="$(iface_for "$NAME")"; ROLE="IRAN"; ID="$id"
  save_meta "$dir" "NAME=$NAME" "ROLE=$ROLE" "ID=$ID" "IFNAME=$IFNAME"     "LOCAL_PUBLIC=$LOCAL_PUBLIC" "REMOTE_PUBLIC=$REMOTE_PUBLIC"     "LOCAL_TUN=$LOCAL_TUN" "REMOTE_TUN=$REMOTE_TUN" "PREFIX=$PREFIX"     "PROFILE=$PROFILE" "MTU=$MTU" "TXQLEN=$TXQLEN"
  prompt_initial_ports "$NAME"
  printf '%s\n' "$(pair_code)" >"$dir/pair.code"; chmod 600 "$dir/pair.code"
  echo
  top; sect "CREATED - $NAME"; blank
  kv "role" "$W IRAN / pair owner$N"
  kv "outer" "$W$LOCAL_PUBLIC -> $REMOTE_PUBLIC$N"
  kv "inner" "$W$LOCAL_TUN/$PREFIX -> $REMOTE_TUN$N"
  kv "profile" "$W$PROFILE$N"
  bot; echo
  if service_start "$NAME"; then ok "GRE interface is up"; else bad "service failed - config and Pair Code were kept"; fi
  show_pair_code "$NAME"
  warn "provider firewall/security-group must allow GRE protocol 47"
  pause
}

new_kharej(){
  header "NEW TUNNEL - KHAREJ"
  top; sect "ROLE CHECK"; blank
  row "$(printf '%sKHAREJ takes the Pair Code created on IRAN.%s' "$D" "$N")"
  row "$(printf '%sGRE settings come from the code - no manual duplicate setup.%s' "$D" "$N")"
  bot; echo
  local code dir detected id
  info "paste the pair code from the IRAN server"
  ask "pair code"; code="$ANS"
  if ! decode_pair "$code"; then
    bad "invalid or unsupported Pair Code"
    dim "copy the complete DGR1-... code from IRAN"
    pause; return
  fi
  echo; top; sect "PAIRED WITH"; blank
  kv "iran" "$W$P_IRAN_PUBLIC$N"
  kv "kharej" "$W$P_KHAREJ_PUBLIC$N"
  kv "inner" "$W$P_KHAREJ_TUN/$P_PREFIX -> $P_IRAN_TUN$N"
  kv "profile" "$W$P_PROFILE$N"
  bot; echo
  NAME="$P_NAME"; dir="$TUN_DIR/$NAME"
  [ ! -e "$dir" ] || { bad "tunnel already exists"; pause; return; }
  detected="$(public_ipv4)"
  [ -n "$detected" ] && [ "$detected" != "$P_KHAREJ_PUBLIC" ] &&
    warn "Pair Code expects $P_KHAREJ_PUBLIC but this server reports $detected"
  LOCAL_PUBLIC="$P_KHAREJ_PUBLIC"; REMOTE_PUBLIC="$P_IRAN_PUBLIC"
  LOCAL_TUN="$P_KHAREJ_TUN"; REMOTE_TUN="$P_IRAN_TUN"
  PREFIX="$P_PREFIX"; PROFILE="$P_PROFILE"; MTU="$P_MTU"; TXQLEN="$P_TXQLEN"
  id="$(printf '%s' "$NAME" | sha256sum | cut -c1-8)"
  IFNAME="$(iface_for "$NAME")"; ROLE="KHAREJ"; ID="$id"
  save_meta "$dir" "NAME=$NAME" "ROLE=$ROLE" "ID=$ID" "IFNAME=$IFNAME"     "LOCAL_PUBLIC=$LOCAL_PUBLIC" "REMOTE_PUBLIC=$REMOTE_PUBLIC"     "LOCAL_TUN=$LOCAL_TUN" "REMOTE_TUN=$REMOTE_TUN" "PREFIX=$PREFIX"     "PROFILE=$PROFILE" "MTU=$MTU" "TXQLEN=$TXQLEN"
  if service_start "$NAME"; then ok "GRE interface is up"; else bad "service failed"; fi
  ping -c 2 -W 2 "$REMOTE_TUN" >/dev/null 2>&1 &&
    ok "inner peer responds: $REMOTE_TUN" ||
    warn "inner peer is not responding yet"
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
  local n="$1" d="$TUN_DIR/$n" p proto target
  . "$d/meta.conf"; [ "$ROLE" = IRAN ] || { warn "forward ports are managed on IRAN"; return; }
  ask "Listen port on IRAN" ""; p="$ANS"; valid_port "$p" || { warn "invalid port"; return; }
  ask "Protocol tcp/udp/both" "tcp"; proto="${ANS,,}"
  ask "Target port on KHAREJ" "$p"; target="$ANS"; valid_port "$target" || { warn "invalid port"; return; }
  case "$proto" in
    tcp|udp) add_port_noninteractive "$n" "$proto" "$p" "$target" ;;
    both) add_port_noninteractive "$n" tcp "$p" "$target"; add_port_noninteractive "$n" udp "$p" "$target" ;;
    *) warn "invalid protocol"; return ;;
  esac
  "$RUNNER" reload-fw "$n" && ok "port map applied"
}

remove_port_menu(){
  local n="$1" d="$TUN_DIR/$n" line tmp
  . "$d/meta.conf"; [ "$ROLE" = IRAN ] || { warn "forward ports are managed on IRAN"; return; }
  [ -s "$d/ports.list" ] || { warn "no ports"; return; }
  nl -ba "$d/ports.list" | sed 's/^/  /'
  ask "Line number to remove" ""; line="$ANS"; [[ "$line" =~ ^[0-9]+$ ]] || return
  tmp="$(mktemp)"; awk -v n="$line" 'NR!=n' "$d/ports.list" >"$tmp"; install -m 600 "$tmp" "$d/ports.list"; rm -f "$tmp"
  "$RUNNER" reload-fw "$n" && ok "port map removed"
}

delete_tunnel(){
  local n="$1" d="$TUN_DIR/$n" v
  read -r -p "  type DELETE to remove $n: " v; [ "$v" = DELETE ] || return
  systemctl disable "darkgre@$n.service" >/dev/null 2>&1 || true
  service_stop "$n"
  rm -rf "$d"
  ok "deleted $n"
}

manage(){
  while true; do
    header "Manage tunnels"; list_tunnels; echo
    ask "Tunnel name (blank = back)" ""; local n="$ANS"; [ -z "$n" ] && return
    [ -r "$TUN_DIR/$n/meta.conf" ] || { warn "not found"; pause; continue; }
    while true; do
      header "Manage · $n"; status_tunnel "$n"; echo
      echo "  [1] Start          [2] Stop           [3] Restart"
      echo "  [4] Add port       [5] Remove port    [6] Ping inner peer"
      echo "  [7] Live log       [8] Delete         [0] Back"
      ask "Select" "0"
      case "$ANS" in
        1) service_start "$n"; pause ;;
        2) service_stop "$n"; pause ;;
        3) service_restart "$n"; pause ;;
        4) add_port_menu "$n"; pause ;;
        5) remove_port_menu "$n"; pause ;;
        6) . "$TUN_DIR/$n/meta.conf"; ping -c 4 -W 2 "$REMOTE_TUN" || true; pause ;;
        7) journalctl -u "darkgre@$n.service" -f --no-pager ;;
        8) delete_tunnel "$n"; pause; break ;;
        0) break ;;
      esac
    done
  done
}

dashboard(){
  header "Dashboard"; list_tunnels; echo
  local d
  shopt -s nullglob
  for d in "$TUN_DIR"/*; do
    [ -r "$d/meta.conf" ] || continue; . "$d/meta.conf"
    if ping -c 1 -W 1 "$REMOTE_TUN" >/dev/null 2>&1; then ok "$NAME inner peer $REMOTE_TUN reachable"; else warn "$NAME inner peer $REMOTE_TUN unreachable"; fi
  done
  shopt -u nullglob
  pause
}

diagnostics(){
  header "Diagnostics"
  command -v ip >/dev/null && ok "iproute2 available" || bad "ip command missing"
  modprobe ip_gre 2>/dev/null && ok "GRE kernel module available" || warn "could not load ip_gre"
  [ "$(sysctl -n net.ipv4.ip_forward 2>/dev/null)" = 1 ] && ok "IPv4 forwarding enabled" || warn "IPv4 forwarding disabled"
  iptables -t nat -L -n >/dev/null 2>&1 && ok "iptables NAT available" || bad "iptables NAT unavailable"
  echo; list_tunnels; pause
}

update_self(){
  header "Update"
  [ -s "$UPDATE_URL_FILE" ] || { warn "no update URL configured"; pause; return; }
  local url tmp ver; url="$(cat "$UPDATE_URL_FILE")"; tmp="$(mktemp)"
  curl -fsSL --retry 3 --max-time 30 -o "$tmp" "$url" || { bad "download failed"; rm -f "$tmp"; pause; return; }
  grep -q 'DARKVPN-GRE-SCRIPT' "$tmp" && bash -n "$tmp" || { bad "invalid update"; rm -f "$tmp"; pause; return; }
  ver="$(grep -m1 '^SCRIPT_VER=' "$tmp" | cut -d'"' -f2)"
  install -m 0755 "$tmp" "$SELF_PATH"; rm -f "$tmp"; ok "updated to $ver"; pause
}

uninstall_all(){
  header "Uninstall"; local v d
  read -r -p "  type UNINSTALL to remove DARK GRE: " v; [ "$v" = UNINSTALL ] || return
  shopt -s nullglob
  for d in "$TUN_DIR"/*; do
    [ -r "$d/meta.conf" ] || continue
    . "$d/meta.conf"
    systemctl disable "darkgre@$NAME.service" >/dev/null 2>&1 || true
    service_stop "$NAME"
  done
  shopt -u nullglob
  rm -f "$UNIT_FILE" "$RUNNER" /etc/sysctl.d/99-dark-gre.conf
  systemctl daemon-reload
  rm -rf "$BASE_DIR"
  ok "DARK GRE removed; manager file kept at $SELF_PATH"
  pause
}

main(){
  need_root
  ensure_deps
  ensure_system
  while :; do
    header
    local tot run
    tot="$(find "$TUN_DIR" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)"
    run="$(systemctl list-units 'darkgre@*' --state=running --no-legend 2>/dev/null | grep -c .)"
    top
    row "$(printf '%s%s%s tunnels   %s%s%s running   %s%s%s' "$W$BD" "$tot" "$N" "$G$BD" "$run" "$N" "$D" "$(gre_core_ready && echo 'core ready' || echo 'core check')" "$N")"
    mid; sect "SETUP"
    item 1 "Core" "GRE kernel / dependencies"
    item 2 "New tunnel - IRAN" "makes Pair Code"
    item 3 "New tunnel - KHAREJ" "takes Pair Code"
    mid; sect "OPERATE"
    item 4 "Manage tunnels" "ports, profile, endpoint"
    item 5 "Dashboard" ""
    item 6 "Diagnostics" "GRE and firewall tests"
    mid; sect "MAINTENANCE"
    item 7 "Update" ""
    item 8 "Uninstall" ""
    item 0 "Exit" ""
    bot; echo; getkey
    case "$KEY" in
      1) diagnostics ;;
      2) new_iran ;;
      3) new_kharej ;;
      4) manage ;;
      5) dashboard ;;
      6) diagnostics ;;
      7) update_self ;;
      8) uninstall_all ;;
      0|q|Q) clear; printf '  %sDARK VPN - GRE Direct%s  %s%s%s\n\n' "$C" "$N" "$D" "$DEV_ID" "$N"; exit 0 ;;
    esac
  done
}
if [ "${DARK_GRE_LIB_ONLY:-0}" != 1 ]; then
  main "$@"
fi