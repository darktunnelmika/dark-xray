#!/usr/bin/env bash
# ==============================================================================
#  DARK VPN · WATERWALL DIRECT
#  Reality / Reality+HalfDuplex direct tunnel manager
#  Support: @mikakhadm
#
#  FLOW:
#    IRAN listens on user-facing TCP ports and initiates transport connections.
#    KHAREJ listens on one transport TCP port and relays to local target ports.
#
#  DARKVPN-WATERWALL-SCRIPT
# ==============================================================================
set -u

SCRIPT_VER="0.1.0-rc1"
DEV_ID="@mikakhadm"
BASE_DIR="${DARK_WW_BASE_DIR:-/etc/dark-waterwall}"
TUN_DIR="$BASE_DIR/tunnels"
WATER_DIR="${DARK_WW_WATER_DIR:-/opt/dark-waterwall}"
WATER_BIN="$WATER_DIR/Waterwall"
WATER_VERSION_FILE="$WATER_DIR/version.txt"
UNIT_FILE="${DARK_WW_UNIT_FILE:-/etc/systemd/system/darkwater@.service}"
UPDATE_URL_FILE="$BASE_DIR/update.url"
SELF_PATH="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "$0")"

if [ "${DARK_WW_LIB_ONLY:-0}" != 1 ] && [ "${DARK_WW_REPAIR_ONLY:-0}" != 1 ] && [ "${DARK_WW_SELFTEST:-0}" != 1 ] && [ ! -t 0 ] && [ -r /dev/tty ]; then
  exec </dev/tty
fi

# ================================================================== UI ======
R=$'\e[38;5;203m'; G=$'\e[38;5;114m'; Y=$'\e[38;5;221m'
C=$'\e[38;5;81m';  M=$'\e[38;5;177m'; W=$'\e[1;97m'
D=$'\e[38;5;244m'; N=$'\e[0m';        BD=$'\e[1m'
UIW=64

ok()   { printf '  %s+%s %s\n' "$G" "$N" "$*"; }
bad()  { printf '  %sx%s %s\n' "$R" "$N" "$*" >&2; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
info() { printf '  %s>%s %s\n' "$C" "$N" "$*"; }
dim()  { printf '    %s%s%s\n' "$D" "$*" "$N"; }
rep()  { local ch="$1" n="$2"; [ "${n:-0}" -gt 0 ] 2>/dev/null || return 0; printf "${ch}%.0s" $(seq 1 "$n"); }
top()  { printf '  %s╭%s╮%s\n' "$C" "$(rep '─' $((UIW+2)))" "$N"; }
mid()  { printf '  %s├%s┤%s\n' "$C" "$(rep '─' $((UIW+2)))" "$N"; }
bot()  { printf '  %s╰%s╯%s\n' "$C" "$(rep '─' $((UIW+2)))" "$N"; }
row()  { local t="$1" clean pad; clean="$(printf '%b' "$t" | sed -E 's/\x1B\[[0-9;]*m//g')"; pad=$((UIW-${#clean})); ((pad<0))&&pad=0; printf '  %s│%s %b%*s %s│%s\n' "$C" "$N" "$t" "$pad" "" "$C" "$N"; }
sect() { row "${M}${BD}$1${N}"; }
item() { row "${Y}[$1]${N}  ${W}$2${N} ${D}${3:-}${N}"; }

ask() {
  local p="$1" d="${2:-}" v
  if [ -n "$d" ]; then read -r -p "$(printf '  %s>%s %s %s[%s]%s: ' "$C" "$N" "$p" "$D" "$d" "$N")" v
  else read -r -p "$(printf '  %s>%s %s: ' "$C" "$N" "$p")" v; fi
  ANS="${v:-$d}"
}
yesno() {
  local p="$1" d="$2" v
  read -r -p "$(printf '  %s>%s %s %s[%s]%s: ' "$C" "$N" "$p" "$D" "$([ "$d" = y ] && echo 'Y/n' || echo 'y/N')" "$N")" v
  v="${v:-$d}"; [[ "$v" =~ ^[Yy]$ ]]
}
getkey() { local k; printf '  %s>%s Select: ' "$C" "$N"; read -rsn1 k; [ -z "$k" ] && k="_"; printf '%s\n\n' "$k"; KEY="$k"; }
pause() { printf '\n  %spress any key%s' "$D" "$N"; read -rsn1 _; echo; }

header() {
  clear 2>/dev/null || true
  top
  row "${W}${BD}D A R K   W A T E R W A L L${N}"
  row "${C}DIRECT REALITY / HALF-DUPLEX${N}"
  mid
  row "${D}v${SCRIPT_VER}${N}   ${M}${DEV_ID}${N}"
  bot
  [ -n "${1:-}" ] && { echo; printf '  %s>%s %s%s%s\n' "$C" "$N" "$W$BD" "$1" "$N"; }
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
valid_host(){ [[ "$1" =~ ^[A-Za-z0-9.-]{1,253}$ ]] && [[ "$1" != .* ]] && [[ "$1" != *..* ]]; }
primary_ipv4(){
  local dev addr
  dev="$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") {print $(i+1); exit}}')"
  [ -n "$dev" ] && addr="$(ip -4 -o addr show dev "$dev" scope global 2>/dev/null | awk '{split($4,a,"/"); print a[1]; exit}')"
  printf '%s\n' "${addr:-}"
}
public_ipv4(){
  local loc pub
  loc="$(primary_ipv4)"
  if valid_ip4 "$loc"; then echo "$loc"; return; fi
  pub="$(curl -4 -fsS --max-time 4 https://api.ipify.org 2>/dev/null || true)"
  valid_ip4 "$pub" && echo "$pub"
}
auto_workers(){
  local n=2
  command -v nproc >/dev/null 2>&1 && n="$(nproc 2>/dev/null || echo 2)"
  [[ "$n" =~ ^[0-9]+$ ]] || n=2
  [ "$n" -lt 2 ] && n=2
  [ "$n" -gt 8 ] && n=8
  echo "$n"
}
gen_secret(){ openssl rand -hex 16; }

ensure_deps(){
  local need=0 c
  for c in curl unzip python3 openssl systemctl ss awk sed grep sha256sum base64; do command -v "$c" >/dev/null 2>&1 || need=1; done
  [ "$need" -eq 0 ] && return 0
  info "installing dependencies"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq >/dev/null 2>&1
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl unzip python3 openssl ca-certificates iproute2 coreutils procps >/dev/null 2>&1
  elif command -v dnf >/dev/null 2>&1; then dnf install -y -q curl unzip python3 openssl ca-certificates iproute coreutils procps-ng >/dev/null 2>&1
  elif command -v yum >/dev/null 2>&1; then yum install -y -q curl unzip python3 openssl ca-certificates iproute coreutils procps-ng >/dev/null 2>&1
  elif command -v apk >/dev/null 2>&1; then apk add --no-cache bash curl unzip python3 openssl ca-certificates iproute2 coreutils procps >/dev/null 2>&1
  else bad "supported package manager not found"; return 1; fi
}

parse_ports_to_file(){
  local spec="$1" out="$2"
  python3 - "$spec" "$out" <<'PY'
import re, sys
spec, out = sys.argv[1], sys.argv[2]
items=[]; intervals=[]; normalized=[]
for raw in spec.split(','):
    tok=raw.strip()
    if not tok:
        continue
    m=re.fullmatch(r'(\d+)', tok)
    if m:
        lp=int(m.group(1)); tp=lp; kind='single'; a=b=lp
        normalized.append(str(lp))
    else:
        m=re.fullmatch(r'(\d+):(\d+)', tok)
        if m:
            lp=int(m.group(1)); tp=int(m.group(2)); kind='single'; a=b=lp
            normalized.append(f'{lp}:{tp}' if lp != tp else str(lp))
        else:
            m=re.fullmatch(r'(\d+)-(\d+)', tok)
            if not m:
                raise SystemExit(f'invalid port token: {tok}')
            a,b=map(int,m.groups())
            if a>b: raise SystemExit(f'invalid range: {tok}')
            lp=tp=None; kind='range'; normalized.append(f'{a}-{b}')
    if not (1 <= a <= 65535 and 1 <= b <= 65535): raise SystemExit(f'listen port out of range: {tok}')
    if kind=='single' and not (10 <= tp <= 65535): raise SystemExit(f'target port must be 10..65535: {tok}')
    if kind=='range' and a < 10: raise SystemExit(f'target range must start at 10 or higher: {tok}')
    for x,y in intervals:
        if max(a,x) <= min(b,y): raise SystemExit(f'overlapping listen ports: {tok}')
    intervals.append((a,b))
    items.append((kind, lp if kind=='single' else a, tp if kind=='single' else b))
if not items: raise SystemExit('at least one port mapping is required')
if len(items) > 128: raise SystemExit('too many mappings; max 128 entries')
with open(out,'w',encoding='utf-8') as f:
    for kind,a,b in items: f.write(f'{kind}\t{a}\t{b}\n')
print(','.join(normalized))
PY
}
ports_to_spec(){
  local f="$1"
  python3 - "$f" <<'PY'
import sys
parts=[]
for line in open(sys.argv[1], encoding='utf-8'):
    kind,a,b=line.rstrip('\n').split('\t')
    if kind=='range': parts.append(f'{a}-{b}')
    else: parts.append(a if a==b else f'{a}:{b}')
print(','.join(parts))
PY
}
target_conflicts_transport(){
  local f="$1" p="$2"
  python3 - "$f" "$p" <<'PY'
import sys
p=int(sys.argv[2])
for line in open(sys.argv[1], encoding='utf-8'):
    kind,a,b=line.rstrip('\n').split('\t'); a=int(a); b=int(b)
    if (kind=='range' and a<=p<=b) or (kind=='single' and b==p): raise SystemExit(0)
raise SystemExit(1)
PY
}

make_pair_code(){
  local name="$1" ir="$2" kh="$3" transport="$4" mode="$5" sni="$6" secret="$7" target="$8" ports="$9"
  python3 - "$name" "$ir" "$kh" "$transport" "$mode" "$sni" "$secret" "$target" "$ports" <<'PY'
import base64, hashlib, json, sys
name,ir,kh,tp,mode,sni,secret,target,ports=sys.argv[1:]
o={"v":1,"name":name,"ir_ip":ir,"kh_ip":kh,"transport_port":int(tp),"mode":mode,"sni":sni,"secret":secret,"target":target,"ports":ports}
raw=json.dumps(o,separators=(',',':'),sort_keys=True).encode()
enc=base64.urlsafe_b64encode(raw).decode().rstrip('=')
chk=hashlib.sha256(raw).hexdigest()[:12]
print('DWW1-'+enc+'.'+chk)
PY
}
decode_pair_code(){
  local code="$1"
  mapfile -t PAIR_FIELDS < <(python3 - "$code" <<'PY'
import base64, hashlib, json, re, sys
code=sys.argv[1].strip()
if not code.startswith('DWW1-') or '.' not in code: raise SystemExit('invalid DWW1 pair code')
enc,chk=code[5:].rsplit('.',1)
try: raw=base64.urlsafe_b64decode(enc+'='*((4-len(enc)%4)%4))
except Exception: raise SystemExit('invalid pair encoding')
if hashlib.sha256(raw).hexdigest()[:12] != chk: raise SystemExit('pair checksum mismatch')
try:o=json.loads(raw)
except Exception: raise SystemExit('invalid pair payload')
required=['v','name','ir_ip','kh_ip','transport_port','mode','sni','secret','target','ports']
if any(k not in o for k in required) or o['v']!=1: raise SystemExit('unsupported pair payload')
if o['mode'] not in ('reality','reality-hd'): raise SystemExit('unsupported pair mode')
if not re.fullmatch(r'[A-Za-z0-9_-]{1,24}',str(o['name'])): raise SystemExit('invalid tunnel name')
if not (1 <= int(o['transport_port']) <= 65535): raise SystemExit('invalid transport port')
if not (1 <= len(str(o['secret']).encode()) <= 32): raise SystemExit('invalid Reality secret')
for k in required: print(o[k])
PY
) || return 1
  [ "${#PAIR_FIELDS[@]}" -eq 10 ] || return 1
}
pair_fingerprint(){
  local name="$1" ir="$2" kh="$3" transport="$4" mode="$5" sni="$6" secret="$7" target="$8" ports="$9"
  printf '%s' "$name|$ir|$kh|$transport|$mode|$sni|$secret|$target|$ports" | sha256sum | awk '{print substr($1,1,16)}'
}

save_meta(){
  local dir="$1"; shift
  mkdir -p "$dir/logs"; chmod 700 "$dir"
  : >"$dir/meta.conf"
  local kv key val
  for kv in "$@"; do key="${kv%%=*}"; val="${kv#*=}"; printf '%s=%q\n' "$key" "$val" >>"$dir/meta.conf"; done
  chmod 600 "$dir/meta.conf"
}

write_runtime(){
  local name="$1" dir
  dir="$TUN_DIR/$name"
  [ -r "$dir/meta.conf" ] || { bad "missing tunnel metadata"; return 1; }
  # shellcheck disable=SC1090
  . "$dir/meta.conf"
  [ -r "$dir/ports.list" ] || { bad "missing ports.list"; return 1; }
  mkdir -p "$dir/logs"
  export DWW_NAME="$NAME" DWW_ROLE="$ROLE" DWW_IR_IP="$IR_IP" DWW_KH_IP="$KH_IP"
  export DWW_TRANSPORT_PORT="$TRANSPORT_PORT" DWW_MODE="$MODE" DWW_SNI="$SNI" DWW_SECRET="$SECRET"
  export DWW_TARGET_ADDR="$TARGET_ADDR" DWW_WORKERS="$WORKERS" DWW_PORTS_FILE="$dir/ports.list" DWW_WATER_DIR="$WATER_DIR"
  python3 - "$dir/core.json" "$dir/config.json" <<'PY'
import json, os, sys
core_path, config_path=sys.argv[1:]
name=os.environ['DWW_NAME']; role=os.environ['DWW_ROLE']; ir=os.environ['DWW_IR_IP']; kh=os.environ['DWW_KH_IP']
tp=int(os.environ['DWW_TRANSPORT_PORT']); mode=os.environ['DWW_MODE']; sni=os.environ['DWW_SNI']; secret=os.environ['DWW_SECRET']
target=os.environ['DWW_TARGET_ADDR']; workers=int(os.environ['DWW_WORKERS']); ports_file=os.environ['DWW_PORTS_FILE']; water_dir=os.environ['DWW_WATER_DIR']
core={
  "log":{"path":"logs/","core":{"loglevel":"INFO","file":"core.log","console":True},"network":{"loglevel":"INFO","file":"network.log","console":True},"dns":{"loglevel":"WARN","file":"dns.log","console":False},"internal":{"loglevel":"WARN","file":"internal.log","console":False}},
  "misc":{"workers":workers,"ram-profile":"client" if role=="IRAN" else "server","mtu":1500,"try-enabling-bbr":True,"libs-path":water_dir.rstrip('/')+"/libs/"},
  "dns":{"domain-strategy":"prefer-ipv4"},
  "configs":["config.json"]
}
nodes=[]
if role=='IRAN':
    entries=[]
    for line in open(ports_file,encoding='utf-8'):
        kind,a,b=line.rstrip('\n').split('\t'); entries.append((kind,int(a),int(b)))
    for i,(kind,a,b) in enumerate(entries,1):
        listen=f'in_{i}'; header=f'header_{i}'; half=f'half_{i}'; reality=f'reality_{i}'; transport=f'transport_{i}'
        settings={"address":"0.0.0.0","nodelay":True,"large-send-buffer":True,"large-recv-buffer":True}
        if kind=='range': settings['port-range']=[a,b]; header_data='src_context->port'
        else: settings['port']=a; header_data='src_context->port' if a==b else b
        nodes.append({"name":listen,"type":"TcpListener","settings":settings,"next":header})
        next_after_header=half if mode=='reality-hd' else reality
        nodes.append({"name":header,"type":"HeaderClient","settings":{"data":header_data},"next":next_after_header})
        if mode=='reality-hd': nodes.append({"name":half,"type":"HalfDuplexClient","settings":{},"next":reality})
        nodes.append({"name":reality,"type":"RealityClient","settings":{"sni":sni,"verify":True,"password":secret,"algorithm":"chacha20-poly1305"},"next":transport})
        nodes.append({"name":transport,"type":"TcpConnector","settings":{"address":kh,"port":tp,"nodelay":True,"large-send-buffer":True,"large-recv-buffer":True,"domain-strategy":"only-ipv4"}})
else:
    after_reality='half_server' if mode=='reality-hd' else 'header_server'
    nodes.append({"name":"transport_in","type":"TcpListener","settings":{"address":"0.0.0.0","port":tp,"nodelay":True,"large-send-buffer":True,"large-recv-buffer":True,"whitelist":[ir+"/32"]},"next":"reality_server"})
    nodes.append({"name":"reality_server","type":"RealityServer","settings":{"destination":"visitor","password":secret,"algorithm":"chacha20-poly1305","sniffing-attempts":8},"next":after_reality})
    if mode=='reality-hd': nodes.append({"name":"half_server","type":"HalfDuplexServer","settings":{},"next":"header_server"})
    nodes.append({"name":"header_server","type":"HeaderServer","settings":{"override":"dest_context->port"},"next":"backend"})
    nodes.append({"name":"backend","type":"TcpConnector","settings":{"address":target,"port":"dest_context->port","nodelay":True,"large-send-buffer":True,"large-recv-buffer":True,"domain-strategy":"prefer-ipv4"}})
    nodes.append({"name":"visitor","type":"TcpConnector","settings":{"address":sni,"port":443,"nodelay":True,"domain-strategy":"prefer-ipv4"}})
config={"name":"dark-waterwall-"+name,"author":"DARK VPN","config-version":1,"core-minimum-version":0,"encrypted":False,"nodes":nodes}
for path,obj in ((core_path,core),(config_path,config)):
    with open(path,'w',encoding='utf-8') as f: json.dump(obj,f,ensure_ascii=False,indent=2); f.write('\n')
PY
  chmod 600 "$dir/core.json" "$dir/config.json"
  python3 -m json.tool "$dir/core.json" >/dev/null && python3 -m json.tool "$dir/config.json" >/dev/null
}

write_unit(){
  mkdir -p "$(dirname "$UNIT_FILE")"
  cat >"$UNIT_FILE" <<UNIT_EOF
[Unit]
Description=DARK WaterWall Direct tunnel %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$TUN_DIR/%i
ExecStart=$WATER_BIN --config:core.