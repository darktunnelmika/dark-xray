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
ExecStart=$WATER_BIN --config:core.json
Restart=always
RestartSec=2
TimeoutStopSec=15
KillSignal=SIGINT
LimitNOFILE=1048576
TasksMax=infinity
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
UNIT_EOF
  systemctl daemon-reload >/dev/null 2>&1 || true
}
ensure_system(){ mkdir -p "$TUN_DIR" "$BASE_DIR" "$WATER_DIR"; chmod 700 "$BASE_DIR" "$TUN_DIR"; write_unit; }

resolve_waterwall_release(){
  local arch="$1" oldcpu="${DARK_WW_OLD_CPU:-0}" release_json
  release_json="$(curl -4 -fsSL --retry 2 --connect-timeout 8 --max-time 30 https://api.github.com/repos/radkesvat/WaterWall/releases/latest 2>/dev/null)" || return 1
  WW_RELEASE_JSON="$release_json" python3 - "$arch" "$oldcpu" <<'PYREL'
import json, os, sys
arch, oldcpu=sys.argv[1], sys.argv[2]=='1'
o=json.loads(os.environ['WW_RELEASE_JSON']); assets={a.get('name',''):a.get('browser_download_url','') for a in o.get('assets',[])}
if arch in ('x86_64','amd64'):
    pref=['Waterwall-linux-gcc-x64-old-cpu.zip','Waterwall-linux-gcc-x64.zip'] if oldcpu else ['Waterwall-linux-gcc-x64.zip','Waterwall-linux-gcc-x64-old-cpu.zip','Waterwall-linux-clang-x64.zip']
elif arch in ('aarch64','arm64'):
    pref=['Waterwall-linux-gcc-arm64-old-cpu.zip','Waterwall-linux-gcc-arm64.zip'] if oldcpu else ['Waterwall-linux-gcc-arm64.zip','Waterwall-linux-gcc-arm64-old-cpu.zip']
else:
    raise SystemExit(2)
for name in pref:
    if assets.get(name):
        digest=''
        for a in o.get('assets',[]):
            if a.get('name')==name:
                digest=(a.get('digest') or '').removeprefix('sha256:'); break
        print(o.get('tag_name','latest')); print(assets[name]); print(name); print(digest); break
else: raise SystemExit(3)
PYREL
}
install_waterwall_core(){
  ensure_deps || return 1
  local arch asset release url td zip root bin info_lines expected_sha actual_sha
  arch="$(uname -m)"
  case "$arch" in x86_64|amd64|aarch64|arm64) :;; *) bad "unsupported architecture: $arch"; return 1;; esac
  if [ -n "${DARK_WW_DOWNLOAD_URL:-}" ]; then
    url="$DARK_WW_DOWNLOAD_URL"; release="${DARK_WW_RELEASE:-custom}"; asset="custom"; expected_sha="${DARK_WW_SHA256:-}"
  elif [ -n "${DARK_WW_RELEASE:-}" ]; then
    release="$DARK_WW_RELEASE"
    case "$arch" in
      x86_64|amd64) [ "${DARK_WW_OLD_CPU:-0}" = 1 ] && asset="Waterwall-linux-gcc-x64-old-cpu.zip" || asset="Waterwall-linux-gcc-x64.zip";;
      aarch64|arm64) [ "${DARK_WW_OLD_CPU:-0}" = 1 ] && asset="Waterwall-linux-gcc-arm64-old-cpu.zip" || asset="Waterwall-linux-gcc-arm64.zip";;
    esac
    url="https://github.com/radkesvat/WaterWall/releases/download/$release/$asset"; expected_sha="${DARK_WW_SHA256:-}"
  else
    mapfile -t info_lines < <(resolve_waterwall_release "$arch" || true)
    if [ "${#info_lines[@]}" -ge 3 ]; then release="${info_lines[0]}"; url="${info_lines[1]}"; asset="${info_lines[2]}"; expected_sha="${info_lines[3]:-}"
    else
      release="latest"
      case "$arch" in x86_64|amd64) asset="Waterwall-linux-gcc-x64.zip";; aarch64|arm64) asset="Waterwall-linux-gcc-arm64.zip";; esac
      url="https://github.com/radkesvat/WaterWall/releases/latest/download/$asset"; expected_sha="${DARK_WW_SHA256:-}"
    fi
  fi
  td="$(mktemp -d)" || return 1; zip="$td/waterwall.zip"
  info "downloading WaterWall $release ($asset)"
  if ! curl -4 -fL --retry 3 --retry-all-errors --connect-timeout 10 --max-time 180 -o "$zip" "$url"; then rm -rf "$td"; bad "WaterWall download failed"; dim "override with DARK_WW_DOWNLOAD_URL if your provider needs a mirror"; return 1; fi
  if [ -n "${expected_sha:-}" ]; then
    actual_sha="$(sha256sum "$zip" | awk '{print $1}')"
    [ "$actual_sha" = "$expected_sha" ] || { rm -rf "$td"; bad "WaterWall SHA-256 mismatch"; return 1; }
    ok "WaterWall SHA-256 verified"
  else
    warn "upstream digest unavailable; archive signature is not independently verified"
  fi
  mkdir -p "$td/extract" || { rm -rf "$td"; return 1; }
  unzip -q "$zip" -d "$td/extract" || { rm -rf "$td"; bad "invalid WaterWall archive"; return 1; }
  bin="$(find "$td/extract" -maxdepth 4 -type f \( -iname 'Waterwall' -o -iname 'WaterWall' \) | head -n1)"
  [ -n "$bin" ] || { rm -rf "$td"; bad "WaterWall executable not found in archive"; return 1; }
  root="$(dirname "$bin")"
  mkdir -p "$WATER_DIR"
  [ -f "$WATER_BIN" ] && cp -f "$WATER_BIN" "$WATER_BIN.bak" 2>/dev/null || true
  cp -a "$root"/. "$WATER_DIR"/ || { rm -rf "$td"; bad "could not install WaterWall"; return 1; }
  if [ ! -f "$WATER_BIN" ]; then
    local found; found="$(find "$WATER_DIR" -maxdepth 2 -type f -iname 'Waterwall' | head -n1)"
    [ -n "$found" ] && cp -f "$found" "$WATER_BIN"
  fi
  chmod 0755 "$WATER_BIN" 2>/dev/null || { rm -rf "$td"; bad "WaterWall binary missing after install"; return 1; }
  printf '%s\n' "$release" >"$WATER_VERSION_FILE"; chmod 644 "$WATER_VERSION_FILE"
  rm -rf "$td"
  ok "WaterWall $release installed"
  return 0
}

waterwall_ready(){ [ -x "$WATER_BIN" ]; }
service_start(){ local n="$1"; write_runtime "$n" || return 1; systemctl enable --now "darkwater@$n.service" >/dev/null 2>&1; }
service_restart(){ local n="$1"; write_runtime "$n" || return 1; systemctl restart "darkwater@$n.service" >/dev/null 2>&1; }
service_stop(){ systemctl stop "darkwater@$1.service" >/dev/null 2>&1 || true; }
service_disable(){ systemctl disable --now "darkwater@$1.service" >/dev/null 2>&1 || true; }

new_iran(){
  header "NEW DIRECT · IRAN"
  waterwall_ready || { warn "WaterWall core is not installed"; install_waterwall_core || { pause; return; }; }
  local ip defw dir spec normalized
  ask "Tunnel name" "direct1"; NAME="$ANS"; valid_name "$NAME" || { bad "invalid name"; pause; return; }
  dir="$TUN_DIR/$NAME"; [ ! -e "$dir" ] || { bad "tunnel already exists"; pause; return; }
  ip="$(public_ipv4)"; ask "IRAN public IPv4" "$ip"; IR_IP="$ANS"; valid_ip4 "$IR_IP" || { bad "invalid IRAN IPv4"; pause; return; }
  ask "KHAREJ public IPv4"; KH_IP="$ANS"; valid_ip4 "$KH_IP" || { bad "invalid KHAREJ IPv4"; pause; return; }
  ask "Transport TCP port on KHAREJ" "443"; TRANSPORT_PORT="$ANS"; valid_port "$TRANSPORT_PORT" || { bad "invalid transport port"; pause; return; }
  echo; top; sect "MODE"; item 1 "Reality Direct" "1 TCP transport per user connection"; item 2 "Reality + HalfDuplex" "2 TCP transports per user connection"; bot; echo; getkey
  case "$KEY" in 2) MODE="reality-hd";; *) MODE="reality";; esac
  ask "Reality visitor/SNI domain" "www.cloudflare.com"; SNI="$ANS"; valid_host "$SNI" || { bad "invalid SNI hostname"; pause; return; }
  ask "KHAREJ backend address" "127.0.0.1"; TARGET_ADDR="$ANS"; { valid_ip4 "$TARGET_ADDR" || valid_host "$TARGET_ADDR"; } || { bad "invalid backend address"; pause; return; }
  echo; dim "Port format: 443,8443:9443,20000-20100"
  ask "IRAN listen ports -> KHAREJ target ports" "443"; spec="$ANS"
  mkdir -p "$dir"; chmod 700 "$dir"
  if ! normalized="$(parse_ports_to_file "$spec" "$dir/ports.list" 2>&1)"; then bad "$normalized"; rm -rf "$dir"; pause; return; fi
  chmod 600 "$dir/ports.list"
  if [[ "$TARGET_ADDR" =~ ^(127\.0\.0\.1|localhost|0\.0\.0\.0)$ ]] && target_conflicts_transport "$dir/ports.list" "$TRANSPORT_PORT"; then
    bad "a backend target resolves to the same port as the KHAREJ transport listener ($TRANSPORT_PORT)"
    rm -rf "$dir"; pause; return
  fi
  defw="$(auto_workers)"; ask "WaterWall workers on this IRAN server" "$defw"; WORKERS="$ANS"; [[ "$WORKERS" =~ ^[0-9]+$ ]] && [ "$WORKERS" -ge 1 ] && [ "$WORKERS" -le 64 ] || { bad "workers must be 1..64"; rm -rf "$dir"; pause; return; }
  SECRET="$(gen_secret)"
  PORTS_SPEC="$normalized"
  PAIR_HASH="$(pair_fingerprint "$NAME" "$IR_IP" "$KH_IP" "$TRANSPORT_PORT" "$MODE" "$SNI" "$SECRET" "$TARGET_ADDR" "$PORTS_SPEC")"
  save_meta "$dir" "NAME=$NAME" "ROLE=IRAN" "IR_IP=$IR_IP" "KH_IP=$KH_IP" "TRANSPORT_PORT=$TRANSPORT_PORT" "MODE=$MODE" "SNI=$SNI" "SECRET=$SECRET" "TARGET_ADDR=$TARGET_ADDR" "WORKERS=$WORKERS" "PAIR_HASH=$PAIR_HASH"
  PAIR_CODE="$(make_pair_code "$NAME" "$IR_IP" "$KH_IP" "$TRANSPORT_PORT" "$MODE" "$SNI" "$SECRET" "$TARGET_ADDR" "$PORTS_SPEC")"
  printf '%s\n' "$PAIR_CODE" >"$dir/pair.code"; chmod 600 "$dir/pair.code"
  write_runtime "$NAME" || { bad "runtime generation failed"; pause; return; }
  if service_start "$NAME"; then ok "IRAN service started"; else warn "service did not start; use Diagnostics"; fi
  echo; top; sect "PAIR CODE · paste on KHAREJ"; bot
  printf "\n  %s%s%s\n" "$Y" "$PAIR_CODE" "$N"
  echo; dim "Treat this Pair Code as a secret: it contains the Reality shared secret."
  pause
}

new_kharej(){
  header "NEW DIRECT · KHAREJ"
  waterwall_ready || { warn "WaterWall core is not installed"; install_waterwall_core || { pause; return; }; }
  local code local_ip dir normalized defw
  ask "Paste DWW1 Pair Code"; code="$ANS"
  if ! decode_pair_code "$code"; then bad "invalid or damaged Pair Code"; pause; return; fi
  NAME="${PAIR_FIELDS[1]}"; IR_IP="${PAIR_FIELDS[2]}"; KH_IP="${PAIR_FIELDS[3]}"; TRANSPORT_PORT="${PAIR_FIELDS[4]}"; MODE="${PAIR_FIELDS[5]}"; SNI="${PAIR_FIELDS[6]}"; SECRET="${PAIR_FIELDS[7]}"; TARGET_ADDR="${PAIR_FIELDS[8]}"; PORTS_SPEC="${PAIR_FIELDS[9]}"
  dir="$TUN_DIR/$NAME"; [ ! -e "$dir" ] || { bad "tunnel already exists: $NAME"; pause; return; }
  local_ip="$(public_ipv4)"
  if valid_ip4 "$local_ip" && [ "$local_ip" != "$KH_IP" ]; then warn "this server appears to be $local_ip but Pair Code expects $KH_IP"; yesno "Continue anyway?" n || return; fi
  if ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)$TRANSPORT_PORT$"; then warn "TCP/$TRANSPORT_PORT already appears to be in use"; yesno "Continue and create config anyway?" n || return; fi
  mkdir -p "$dir"; chmod 700 "$dir"
  if ! normalized="$(parse_ports_to_file "$PORTS_SPEC" "$dir/ports.list" 2>&1)"; then bad "$normalized"; rm -rf "$dir"; pause; return; fi
  chmod 600 "$dir/ports.list"
  PORTS_SPEC="$normalized"; defw="$(auto_workers)"; ask "WaterWall workers on this KHAREJ server" "$defw"; WORKERS="$ANS"; [[ "$WORKERS" =~ ^[0-9]+$ ]] && [ "$WORKERS" -ge 1 ] && [ "$WORKERS" -le 64 ] || { bad "workers must be 1..64"; rm -rf "$dir"; pause; return; }
  PAIR_HASH="$(pair_fingerprint "$NAME" "$IR_IP" "$KH_IP" "$TRANSPORT_PORT" "$MODE" "$SNI" "$SECRET" "$TARGET_ADDR" "$PORTS_SPEC")"
  save_meta "$dir" "NAME=$NAME" "ROLE=KHAREJ" "IR_IP=$IR_IP" "KH_IP=$KH_IP" "TRANSPORT_PORT=$TRANSPORT_PORT" "MODE=$MODE" "SNI=$SNI" "SECRET=$SECRET" "TARGET_ADDR=$TARGET_ADDR" "WORKERS=$WORKERS" "PAIR_HASH=$PAIR_HASH"
  printf '%s\n' "$code" >"$dir/pair.code"; chmod 600 "$dir/pair.code"
  write_runtime "$NAME" || { bad "runtime generation failed"; pause; return; }
  if service_start "$NAME"; then ok "KHAREJ service started"; else warn "service did not start; use Diagnostics"; fi
  echo; dim "Transport TCP/$TRANSPORT_PORT accepts only source $IR_IP/32 inside WaterWall."
  pause
}

list_tunnels(){
  local d any=0 state role mode ports
  shopt -s nullglob
  for d in "$TUN_DIR"/*; do
    [ -r "$d/meta.conf" ] || continue; any=1
    unset NAME ROLE MODE TRANSPORT_PORT
    # shellcheck disable=SC1090
    . "$d/meta.conf"
    state="$(systemctl is-active "darkwater@$NAME.service" 2>/dev/null || true)"
    ports="$(ports_to_spec "$d/ports.list" 2>/dev/null || echo '?')"
    printf '  %-20s %-7s %-11s %-12s transport:%-5s ports:%s\n' "$NAME" "${ROLE:-?}" "${MODE:-?}" "${state:-inactive}" "${TRANSPORT_PORT:-?}" "$ports"
  done
  shopt -u nullglob
  [ "$any" -eq 1 ] || dim "no tunnels"
}
choose_tunnel(){
  local names=() d i=1 v
  shopt -s nullglob
  for d in "$TUN_DIR"/*; do [ -r "$d/meta.conf" ] && names+=("$(basename "$d")"); done
  shopt -u nullglob
  [ "${#names[@]}" -gt 0 ] || { warn "no tunnels"; return 1; }
  for v in "${names[@]}"; do printf '  [%d] %s\n' "$i" "$v"; i=$((i+1)); done
  ask "Tunnel number" "1"; [[ "$ANS" =~ ^[0-9]+$ ]] && [ "$ANS" -ge 1 ] && [ "$ANS" -le "${#names[@]}" ] || return 1
  CHOSEN="${names[$((ANS-1))]}"
}

show_tunnel(){
  local n="$1" dir ports state ver
  dir="$TUN_DIR/$n"
  # shellcheck disable=SC1090
  . "$dir/meta.conf"; ports="$(ports_to_spec "$dir/ports.list")"; state="$(systemctl is-active "darkwater@$n.service" 2>/dev/null || true)"; ver="$(cat "$WATER_VERSION_FILE" 2>/dev/null || echo unknown)"
  top; sect "$NAME"; row "Role: ${W}${ROLE}${N}"; row "Mode: ${W}${MODE}${N}"; row "Service: ${W}${state}${N}"; row "IRAN: ${IR_IP}"; row "KHAREJ: ${KH_IP}:${TRANSPORT_PORT}"; row "SNI: ${SNI}"; row "Backend: ${TARGET_ADDR}"; row "Ports: ${ports}"; row "Workers: ${WORKERS}"; row "WaterWall: ${ver}"; row "Pair: ${PAIR_HASH}"; bot
}

edit_ports(){
  local n="$1" dir spec normalized
  dir="$TUN_DIR/$n"
  # shellcheck disable=SC1090
  . "$dir/meta.conf"
  [ "$ROLE" = IRAN ] || { warn "ports are controlled from the IRAN side"; return; }
  dim "Current: $(ports_to_spec "$dir/ports.list")"; ask "New ports" "$(ports_to_spec "$dir/ports.list")"; spec="$ANS"
  if ! normalized="$(parse_ports_to_file "$spec" "$dir/ports.list.new" 2>&1)"; then bad "$normalized"; rm -f "$dir/ports.list.new"; return; fi
  mv "$dir/ports.list.new" "$dir/ports.list"; chmod 600 "$dir/ports.list"; PORTS_SPEC="$normalized"
  PAIR_HASH="$(pair_fingerprint "$NAME" "$IR_IP" "$KH_IP" "$TRANSPORT_PORT" "$MODE" "$SNI" "$SECRET" "$TARGET_ADDR" "$PORTS_SPEC")"
  sed -i "s|^PAIR_HASH=.*|PAIR_HASH=$(printf %q "$PAIR_HASH")|" "$dir/meta.conf"
  PAIR_CODE="$(make_pair_code "$NAME" "$IR_IP" "$KH_IP" "$TRANSPORT_PORT" "$MODE" "$SNI" "$SECRET" "$TARGET_ADDR" "$PORTS_SPEC")"; printf '%s\n' "$PAIR_CODE" >"$dir/pair.code"; chmod 600 "$dir/pair.code"
  service_restart "$n" && ok "ports applied" || bad "restart failed"
}

show_pair_code(){ local n="$1"; [ -r "$TUN_DIR/$n/pair.code" ] || { warn "Pair Code unavailable"; return; }; echo; cat "$TUN_DIR/$n/pair.code"; echo; dim "Pair Code contains the shared Reality secret."; }

delete_tunnel(){ local n="$1"; yesno "Delete $n?" n || return; service_disable "$n"; rm -rf "$TUN_DIR/$n"; ok "deleted $n"; }

manage(){
  header "MANAGE TUNNELS"; list_tunnels; echo; choose_tunnel || { pause; return; }
  local n="$CHOSEN"
  while :; do
    header "MANAGE · $n"; show_tunnel "$n"; echo
    top; sect "CONTROL"; item 1 "Start" ""; item 2 "Stop" ""; item 3 "Restart" ""; mid; sect "CONFIGURE"; item 4 "Ports" "IRAN side"; item 5 "Pair Code" "secret"; mid; sect "INSPECT"; item 6 "Diagnostics" ""; item 7 "Logs" "last 80 lines"; mid; item 8 "Delete" ""; item 0 "Back" ""; bot; echo; getkey
    case "$KEY" in
      1) service_start "$n" && ok "started" || bad "start failed"; pause;;
      2) service_stop "$n"; ok "stopped"; pause;;
      3) service_restart "$n" && ok "restarted" || bad "restart failed"; pause;;
      4) edit_ports "$n"; pause;;
      5) show_pair_code "$n"; pause;;
      6) diagnostics_one "$n"; pause;;
      7) journalctl -u "darkwater@$n.service" -n 80 --no-pager 2>/dev/null || true; pause;;
      8) delete_tunnel "$n"; pause; return;;
      0) return;;
    esac
  done
}

diagnostics_one(){
  local n="$1" dir state conns fds portsok=1
  dir="$TUN_DIR/$n"
  [ -r "$dir/meta.conf" ] || return 1
  # shellcheck disable=SC1090
  . "$dir/meta.conf"
  state="$(systemctl is-active "darkwater@$n.service" 2>/dev/null || true)"
  echo; info "service: ${state:-inactive}"
  if python3 -m json.tool "$dir/core.json" >/dev/null 2>&1 && python3 -m json.tool "$dir/config.json" >/dev/null 2>&1; then ok "JSON syntax"; else bad "JSON syntax"; fi
  if [ "$ROLE" = KHAREJ ]; then
    if ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)$TRANSPORT_PORT$"; then ok "transport listening TCP/$TRANSPORT_PORT"; else bad "transport not listening TCP/$TRANSPORT_PORT"; fi
  else
    while IFS=$'\t' read -r kind a b; do
      if [ "$kind" = single ]; then ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)$a$" || portsok=0
      else ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)$a$" || portsok=0; fi
    done <"$dir/ports.list"
    [ "$portsok" -eq 1 ] && ok "IRAN listener ports visible" || warn "one or more IRAN listener ports are not visible"
  fi
  conns="$(ss -ntpH 2>/dev/null | grep -ci '[Ww]aterwall' || true)"; info "WaterWall TCP sockets: $conns"
  local pid; pid="$(systemctl show -p MainPID --value "darkwater@$n.service" 2>/dev/null || echo 0)"; if [[ "$pid" =~ ^[0-9]+$ ]] && [ "$pid" -gt 0 ] && [ -d "/proc/$pid/fd" ]; then fds="$(find "/proc/$pid/fd" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l)"; info "open file descriptors: $fds / 1048576"; fi
  info "recent log:"; journalctl -u "darkwater@$n.service" -n 20 --no-pager 2>/dev/null || true
}

diagnostics_menu(){ header "DIAGNOSTICS"; list_tunnels; echo; choose_tunnel || { pause; return; }; diagnostics_one "$CHOSEN"; pause; }
dashboard(){ header "DASHBOARD"; list_tunnels; echo; local running total; total="$(find "$TUN_DIR" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)"; running="$(systemctl list-units 'darkwater@*.service' --state=running --no-legend 2>/dev/null | wc -l)"; info "tunnels: $total  running: $running"; info "WaterWall: $(cat "$WATER_VERSION_FILE" 2>/dev/null || echo not-installed)"; pause; }

update_waterwall(){
  local names=() d
  shopt -s nullglob; for d in "$TUN_DIR"/*; do [ -d "$d" ] && names+=("$(basename "$d")"); done; shopt -u nullglob
  install_waterwall_core || { pause; return; }
  for d in "${names[@]}"; do systemctl try-restart "darkwater@$d.service" >/dev/null 2>&1 || true; done
  ok "WaterWall core updated; active tunnels restarted"; pause
}

update_self(){
  local tmp url urls=()
  tmp="$(mktemp)" || return
  [ -r "$UPDATE_URL_FILE" ] && urls+=("$(cat "$UPDATE_URL_FILE")")
  urls+=("https://cdn.jsdelivr.net/gh/darktunnelmika/dark-xray@main/standalone/dark-waterwall/dark-waterwall.sh" "https://raw.githubusercontent.com/darktunnelmika/dark-xray/main/standalone/dark-waterwall/dark-waterwall.sh")
  for url in "${urls[@]}"; do
    [ -n "$url" ] || continue
    if curl -4 -fsSL --retry 2 --connect-timeout 8 --max-time 60 "$url" -o "$tmp" && grep -q 'DARKVPN-WATERWALL-SCRIPT' "$tmp" && bash -n "$tmp"; then
      install -m 0755 "$tmp" "$SELF_PATH"; printf '%s\n' "$url" >"$UPDATE_URL_FILE"; rm -f "$tmp"; ok "DARK WaterWall script updated"; return 0
    fi
  done
  rm -f "$tmp"; bad "script update failed"
}

uninstall_all(){
  header "UNINSTALL"; warn "This removes DARK WaterWall tunnels and the WaterWall core."; yesno "Continue?" n || return
  local d n
  shopt -s nullglob; for d in "$TUN_DIR"/*; do n="$(basename "$d")"; service_disable "$n"; done; shopt -u nullglob
  rm -f "$UNIT_FILE"; systemctl daemon-reload >/dev/null 2>&1 || true
  rm -rf "$BASE_DIR" "$WATER_DIR"
  [ "$SELF_PATH" = "/usr/local/bin/darkwater" ] && rm -f "$SELF_PATH"
  ok "uninstalled"
}

selftest(){
  local td="$BASE_DIR/selftest" spec code
  mkdir -p "$td/iran" "$td/kharej"
  spec="$(parse_ports_to_file '18443,18444:19444,20000-20010' "$td/ports.list")" || exit 1
  [ "$spec" = '18443,18444:19444,20000-20010' ] || exit 2
  code="$(make_pair_code test 192.0.2.10 198.51.100.20 21443 reality-hd www.example.com 0123456789abcdef0123456789abcdef 127.0.0.1 "$spec")" || exit 3
  decode_pair_code "$code" || exit 4
  [ "${PAIR_FIELDS[5]}" = reality-hd ] || exit 5
  NAME=test; ROLE=IRAN; IR_IP=192.0.2.10; KH_IP=198.51.100.20; TRANSPORT_PORT=21443; MODE=reality-hd; SNI=www.example.com; SECRET=0123456789abcdef0123456789abcdef; TARGET_ADDR=127.0.0.1; WORKERS=2; PAIR_HASH=x
  mkdir -p "$TUN_DIR/test"; cp "$td/ports.list" "$TUN_DIR/test/ports.list"; save_meta "$TUN_DIR/test" "NAME=$NAME" "ROLE=$ROLE" "IR_IP=$IR_IP" "KH_IP=$KH_IP" "TRANSPORT_PORT=$TRANSPORT_PORT" "MODE=$MODE" "SNI=$SNI" "SECRET=$SECRET" "TARGET_ADDR=$TARGET_ADDR" "WORKERS=$WORKERS" "PAIR_HASH=$PAIR_HASH"; write_runtime test || exit 6
  grep -q 'HalfDuplexClient' "$TUN_DIR/test/config.json" || exit 7
  cp "$TUN_DIR/test/core.json" "$td/iran/core.json"; cp "$TUN_DIR/test/config.json" "$td/iran/config.json"; mkdir -p "$td/iran/logs"
  ROLE=KHAREJ; save_meta "$TUN_DIR/test" "NAME=$NAME" "ROLE=$ROLE" "IR_IP=$IR_IP" "KH_IP=$KH_IP" "TRANSPORT_PORT=$TRANSPORT_PORT" "MODE=$MODE" "SNI=$SNI" "SECRET=$SECRET" "TARGET_AD