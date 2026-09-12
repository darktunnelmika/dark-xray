#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

REPO="https://github.com/darktunnelmika/dark-xray.git"
BRANCH="main"
TMP=""
C_RESET='\033[0m'; C_CYAN='\033[38;5;51m'; C_BLUE='\033[38;5;39m'; C_PURPLE='\033[38;5;141m'; C_GREEN='\033[38;5;46m'; C_YELLOW='\033[38;5;226m'; C_RED='\033[38;5;196m'; C_DIM='\033[2m'

cleanup(){ [[ -n "${TMP:-}" && -d "$TMP" ]] && rm -rf "$TMP" || true; }
trap cleanup EXIT

repeat(){ local ch="$1" n="$2" i; for ((i=0;i<n;i++)); do printf '%s' "$ch"; done; }
line(){ repeat '═' 74; printf '\n'; }
banner(){
  clear 2>/dev/null || true
  printf "${C_CYAN}╔"; repeat '═' 74; printf "╗${C_RESET}\n"
  printf "${C_CYAN}║${C_RESET}${C_PURPLE}%74s${C_RESET}${C_CYAN}║${C_RESET}\n" "D A R K   X R A Y"
  printf "${C_CYAN}║${C_RESET}${C_BLUE}%74s${C_RESET}${C_CYAN}║${C_RESET}\n" "100-STEP CYBER INSTALLER"
  printf "${C_CYAN}╚"; repeat '═' 74; printf "╝${C_RESET}\n"
  printf "${C_DIM}Standalone panel • Own DB/API/UI • Xray-core engine • No Sanayi runtime${C_RESET}\n\n"
}
progress(){
  local n="$1" text="$2"
  local filled empty
  filled=$((n/5))
  empty=$((20-filled))
  (( filled > 20 )) && filled=20
  (( empty < 0 )) && empty=0
  printf "${C_CYAN}[%03d/100]${C_RESET} [" "$n"
  repeat '█' "$filled"
  repeat '░' "$empty"
  printf "] %s\n" "$text"
}
fail(){ printf "${C_RED}[FAILED]${C_RESET} %s\n" "$*" >&2; exit 1; }
ok(){ printf "${C_GREEN}[OK]${C_RESET} %s\n" "$*"; }
warn(){ printf "${C_YELLOW}[WARN]${C_RESET} %s\n" "$*"; }
ask(){ local prompt="$1" def="${2:-}" out; if [[ -n "$def" ]]; then read -r -p "$prompt [$def]: " out; printf '%s' "${out:-$def}"; else read -r -p "$prompt: " out; printf '%s' "$out"; fi; }
yesno(){ local prompt="$1" def="${2:-y}" a; read -r -p "$prompt [${def^^}/$([[ $def == y ]] && echo n || echo y)]: " a; a="${a:-$def}"; [[ "$a" =~ ^[Yy]$ ]]; }
valid_port(){ [[ "$1" =~ ^[0-9]+$ ]] && (( 1 <= 10#$1 && 10#$1 <= 65535 )); }
valid_user(){ [[ "$1" =~ ^[A-Za-z0-9_.@+-]{1,128}$ ]]; }
valid_domain(){ [[ "$1" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]]; }
port_busy(){ ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)$1$"; }
public_ipv4(){ curl -4fsS --max-time 5 https://api.ipify.org 2>/dev/null || ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++)if($i=="src"){print $(i+1);exit}}'; }
ssh_port(){ if command -v sshd >/dev/null 2>&1; then sshd -T 2>/dev/null | awk '/^port /{print $2;exit}'; else echo 22; fi; }

state_from_flags(){
  local app="$1" conf="$2" data="$3" wrapper="$4" unit="$5"
  if (( app && conf && data && wrapper && unit )); then
    printf 'installed'
  elif (( app || conf || data || wrapper || unit )); then
    printf 'partial'
  else
    printf 'clean'
  fi
}
install_state(){
  local app=0 conf=0 data=0 wrapper=0 unit=0
  [[ -d /opt/dark-xray && -x /opt/dark-xray/.venv/bin/python ]] && app=1
  [[ -f /etc/dark-xray/config.json ]] && conf=1
  [[ -d /var/lib/dark-xray ]] && data=1
  [[ -x /usr/local/bin/darkxray ]] && wrapper=1
  [[ -f /etc/systemd/system/dark-xray.service ]] && unit=1
  state_from_flags "$app" "$conf" "$data" "$wrapper" "$unit"
}
show_install_artifacts(){
  printf '  %-34s %s\n' '/opt/dark-xray' "$([[ -d /opt/dark-xray ]] && echo PRESENT || echo missing)"
  printf '  %-34s %s\n' '/etc/dark-xray/config.json' "$([[ -f /etc/dark-xray/config.json ]] && echo PRESENT || echo missing)"
  printf '  %-34s %s\n' '/var/lib/dark-xray' "$([[ -d /var/lib/dark-xray ]] && echo PRESENT || echo missing)"
  printf '  %-34s %s\n' '/usr/local/bin/darkxray' "$([[ -x /usr/local/bin/darkxray ]] && echo PRESENT || echo missing)"
  printf '  %-34s %s\n' 'dark-xray.service' "$([[ -f /etc/systemd/system/dark-xray.service ]] && echo PRESENT || echo missing)"
}
repair_partial_install(){
  local stamp recovery
  stamp="$(date +%Y%m%d-%H%M%S)"
  recovery="/root/dark-xray-partial-recovery-$stamp"
  mkdir -p "$recovery"
  progress 3 "Preserving partial configuration/data"
  systemctl stop dark-xray.service >/dev/null 2>&1 || true
  systemctl stop dark-xray-guard.service >/dev/null 2>&1 || true
  [[ -e /etc/dark-xray ]] && mv /etc/dark-xray "$recovery/etc-dark-xray"
  [[ -e /var/lib/dark-xray ]] && mv /var/lib/dark-xray "$recovery/var-lib-dark-xray"
  if [[ -d /opt/dark-xray ]]; then
    tar -C /opt -czf "$recovery/opt-dark-xray-source.tar.gz" \
      --exclude='dark-xray/.venv' --exclude='dark-xray/__pycache__' dark-xray 2>/dev/null || true
    rm -rf /opt/dark-xray
  fi
  rm -f /usr/local/bin/darkxray
  rm -f /etc/systemd/system/dark-xray.service /etc/systemd/system/dark-xray-guard.service
  systemctl daemon-reload >/dev/null 2>&1 || true
  systemctl reset-failed >/dev/null 2>&1 || true
  ok "Partial state preserved at: $recovery"
}

if [[ "${1:-}" == "--selftest" ]]; then
  banner
  progress 1 "Runtime helper self-test"
  progress 100 "Progress renderer self-test"
  valid_port 2087 || fail "valid_port rejected 2087"
  ! valid_port 70000 || fail "valid_port accepted 70000"
  valid_user dark || fail "valid_user rejected dark"
  valid_domain panel.example.com || fail "valid_domain rejected panel.example.com"
  [[ "$(state_from_flags 0 0 0 0 0)" == clean ]] || fail "clean-state classifier failed"
  [[ "$(state_from_flags 1 0 1 0 0)" == partial ]] || fail "partial-state classifier failed"
  [[ "$(state_from_flags 1 1 1 1 1)" == installed ]] || fail "installed-state classifier failed"
  ok "Installer runtime self-test passed"
  exit 0
fi

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail "Run as root: sudo bash /tmp/dark-xray-install.sh"
[[ -d /run/systemd/system ]] || fail "A Linux VPS with systemd is required."
command -v apt-get >/dev/null 2>&1 || fail "This installer currently supports Ubuntu/Debian (apt)."

banner
progress 1 "Preflight checks"
STATE="$(install_state)"
if [[ "$STATE" == installed ]]; then
  warn "A complete DARK XRAY installation was detected."
  echo "  1) Safe update existing installation"
  echo "  2) Open current manager"
  echo "  3) Show installation status"
  echo "  0) Exit"
  choice="$(ask 'Choose' '1')"
  case "$choice" in
    1)
      progress 5 "Installing update prerequisites"
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -qq
      apt-get install -y -q git ca-certificates python3 >/dev/null
      TMP="$(mktemp -d /tmp/dark-xray-update.XXXXXX)"
      progress 15 "Downloading verified project source"
      git clone --depth 1 --branch "$BRANCH" "$REPO" "$TMP/src" >/dev/null 2>&1 || fail "GitHub clone failed"
      progress 35 "Creating rollback snapshot and applying safe update"
      python3 "$TMP/src/tools/update.py" --source "$TMP/src" --non-interactive || fail "Update failed; see messages above"
      progress 100 "Update complete"
      ok "Run: darkxray"
      exit 0
      ;;
    2) exec /usr/local/bin/darkxray ;;
    3) show_install_artifacts; exit 0 ;;
    *) exit 0 ;;
  esac
elif [[ "$STATE" == partial ]]; then
  warn "A PARTIAL/FAILED DARK XRAY installation was detected."
  show_install_artifacts
  echo
  echo "  1) Repair partial install and continue fresh installation"
  echo "  2) Show paths only and exit"
  echo "  0) Exit"
  choice="$(ask 'Choose' '1')"
  case "$choice" in
    1)
      repair_partial_install
      STATE="clean"
      ;;
    2) exit 0 ;;
    *) exit 0 ;;
  esac
fi

progress 5 "Detecting server network and SSH"
DETECTED_IP="$(public_ipv4 || true)"; DETECTED_SSH="$(ssh_port || true)"; DETECTED_SSH="${DETECTED_SSH:-22}"
printf "  Public IP : %s\n  SSH port   : %s\n\n" "${DETECTED_IP:-not detected}" "$DETECTED_SSH"

echo "Install profile:"
echo "  1) Domain + HTTPS/TLS  (recommended)"
echo "  2) IP + SSH tunnel     (no public panel listener)"
echo "  3) Advanced            (custom panel/core settings)"
MODE="$(ask 'Choose profile' '1')"
[[ "$MODE" =~ ^[123]$ ]] || fail "Invalid install profile"

OWNER="$(ask 'Owner username' 'dark')"; valid_user "$OWNER" || fail "Invalid owner username"
PANEL_PORT="$(ask 'Panel port' '2087')"; valid_port "$PANEL_PORT" || fail "Invalid panel port"
(( PANEL_PORT >= 1024 )) || fail "Panel port must be >= 1024"
[[ "$PANEL_PORT" != "$DETECTED_SSH" && "$PANEL_PORT" != "10085" ]] || fail "Panel port conflicts with SSH/Xray API"
port_busy "$PANEL_PORT" && fail "Panel port $PANEL_PORT is already in use"
PUBLIC_ADDRESS="$(ask 'Public proxy IP/DNS' "${DETECTED_IP:-127.0.0.1}")"; [[ -n "$PUBLIC_ADDRESS" ]] || fail "Public address required"
CORE_VERSION="v26.3.27"
if [[ "$MODE" == 3 ]]; then CORE_VERSION="$(ask 'Stable Xray-core version' "$CORE_VERSION")"; [[ "$CORE_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "Invalid Xray version"; fi
DOMAIN=""; EMAIL=""
if [[ "$MODE" == 1 ]]; then
  while :; do DOMAIN="$(ask 'Panel domain (A/AAAA record must point here)')"; valid_domain "$DOMAIN" && break; warn "Invalid domain"; done
  while :; do EMAIL="$(ask 'ACME email')"; [[ "$EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] && break; warn "Invalid email"; done
fi

echo
printf "${C_PURPLE}INSTALL PLAN${C_RESET}\n"
printf "  Owner        : %s\n  Panel port   : %s\n  Proxy address: %s\n  Xray core    : %s\n" "$OWNER" "$PANEL_PORT" "$PUBLIC_ADDRESS" "$CORE_VERSION"
[[ -n "$DOMAIN" ]] && printf "  Domain       : %s\n  HTTPS URL    : https://%s:%s\n" "$DOMAIN" "$DOMAIN" "$PANEL_PORT"
printf "  SSH port     : %s\n" "$DETECTED_SSH"
yesno "Start installation?" y || exit 0

export DEBIAN_FRONTEND=noninteractive
progress 10 "Updating package index"
apt-get update -qq
progress 18 "Installing system prerequisites"
apt-get install -y -q git curl ca-certificates python3 python3-venv unzip openssl iproute2 >/dev/null
[[ "$MODE" == 1 ]] && apt-get install -y -q certbot >/dev/null

TMP="$(mktemp -d /tmp/dark-xray-install.XXXXXX)"
progress 25 "Cloning DARK XRAY from GitHub"
git clone --depth 1 --branch "$BRANCH" "$REPO" "$TMP/src" >/dev/null 2>&1 || fail "GitHub clone failed"
cd "$TMP/src"

progress 35 "Checking source before provisioning"
python3 tools/repo-check.py >/dev/null || fail "Repository hygiene check failed"

progress 45 "Provisioning isolated service account and application"
bash setup.sh --public-address "$PUBLIC_ADDRESS" --ssh-port "$DETECTED_SSH" --username "$OWNER" --port "$PANEL_PORT" --core-version "$CORE_VERSION" --install-os-packages

progress 65 "Verifying systemd services and Xray core"
systemctl is-enabled dark-xray.service >/dev/null || fail "dark-xray service is not enabled"
systemctl is-active dark-xray.service >/dev/null || fail "dark-xray service is not active"
/usr/local/bin/darkxray check >/dev/null || fail "DARK core check failed"

if [[ "$MODE" == 1 ]]; then
  progress 72 "Checking DNS for $DOMAIN"
  RESOLVED="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}' || true)"
  if [[ -n "$DETECTED_IP" && -n "$RESOLVED" && "$RESOLVED" != "$DETECTED_IP" ]]; then
    warn "$DOMAIN resolves to $RESOLVED but detected public IPv4 is $DETECTED_IP"
    yesno "Continue certificate request anyway?" n || { warn "Panel installed; TLS skipped. Run darkxray later to configure domain."; DOMAIN=""; }
  fi
fi

if [[ -n "$DOMAIN" ]]; then
  progress 80 "Issuing Let's Encrypt certificate"
  if port_busy 80; then fail "Port 80 is busy. Panel is installed, but TLS cannot be issued until port 80 is free."; fi
  /usr/local/bin/darkxray domain --domain "$DOMAIN" --email "$EMAIL" --port "$PANEL_PORT" --agree-tos
  progress 90 "Validating HTTPS configuration"
  grep -q '"secure_cookie": true' /etc/dark-xray/config.json || fail "TLS config validation failed"
fi

if yesno "Enable BBR congestion control?" y; then
  progress 94 "Applying BBR tuning"
  cat >/etc/sysctl.d/99-dark-xray-bbr.conf <<'SYSCTL'
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
SYSCTL
  sysctl --system >/dev/null 2>&1 || warn "BBR sysctl could not be fully applied"
fi

progress 97 "Running final doctor"
/usr/local/bin/darkxray doctor || warn "Doctor reported warnings; review them before production use"

progress 100 "DARK XRAY installation complete"
printf "\n${C_GREEN}╔════════════════ INSTALL COMPLETE ════════════════╗${C_RESET}\n"
if [[ -n "$DOMAIN" ]]; then
  printf "${C_GREEN}║${C_RESET} Panel: https://%s:%s\n" "$DOMAIN" "$PANEL_PORT"
else
  printf "${C_GREEN}║${C_RESET} Panel stays loopback-only for safety.\n"
  printf "${C_GREEN}║${C_RESET} SSH: ssh -L %s:127.0.0.1:%s root@SERVER -p %s\n" "$PANEL_PORT" "$PANEL_PORT" "$DETECTED_SSH"
  printf "${C_GREEN}║${C_RESET} Open: http://127.0.0.1:%s\n" "$PANEL_PORT"
fi
printf "${C_GREEN}║${C_RESET} Manager: darkxray\n"
printf "${C_GREEN}╚══════════════════════════════════════════════════╝${C_RESET}\n"