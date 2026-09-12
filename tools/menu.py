#!/usr/bin/env python3
"""DARK XRAY interactive terminal control center.

The menu intentionally delegates privileged operations to explicit system tools
and DARK's existing helpers. It never prints stored passwords, API tokens or
private keys.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IS_INSTALLED = ROOT == Path("/opt/dark-xray")
COMMAND = Path("/usr/local/bin/darkxray") if IS_INSTALLED else ROOT / "darkxray"
CONFIG = Path(os.environ.get("DARK_CONFIG", "/etc/dark-xray/config.json" if IS_INSTALLED else ROOT / "config.json"))
DATA = Path(os.environ.get("DARK_DATA", "/var/lib/dark-xray" if IS_INSTALLED else ROOT / "data"))
VERSION_FILE = ROOT / "VERSION"

# ANSI palette. NO_COLOR is respected for serial consoles and log captures.
USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
RESET = "\033[0m" if USE_COLOR else ""
BOLD = "\033[1m" if USE_COLOR else ""
DIM = "\033[2m" if USE_COLOR else ""
GREEN = "\033[38;5;46m" if USE_COLOR else ""
CYAN = "\033[38;5;51m" if USE_COLOR else ""
BLUE = "\033[38;5;39m" if USE_COLOR else ""
PURPLE = "\033[38;5;141m" if USE_COLOR else ""
YELLOW = "\033[38;5;226m" if USE_COLOR else ""
RED = "\033[38;5;196m" if USE_COLOR else ""
GRAY = "\033[38;5;245m" if USE_COLOR else ""


def clear() -> None:
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")


def run(args, *, check=False, capture=False, env=None):
    try:
        return subprocess.run(
            [str(x) for x in args],
            check=check,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
            env=env,
        )
    except FileNotFoundError:
        print(f"{RED}Command not found:{RESET} {args[0]}")
        return None
    except subprocess.CalledProcessError as ex:
        print(f"{RED}Command failed{RESET} (exit {ex.returncode})")
        return ex


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def service_state(name: str) -> str:
    if not command_exists("systemctl"):
        return "n/a"
    cp = run(["systemctl", "is-active", name], capture=True)
    return (cp.stdout or "").strip() if cp else "unknown"


def enabled_state(name: str) -> str:
    if not command_exists("systemctl"):
        return "n/a"
    cp = run(["systemctl", "is-enabled", name], capture=True)
    return (cp.stdout or "").strip() if cp else "unknown"


def badge(state: str) -> str:
    if state == "active":
        return f"{GREEN}● ONLINE{RESET}"
    if state in {"inactive", "failed", "deactivating"}:
        return f"{RED}● {state.upper()}{RESET}"
    return f"{YELLOW}● {state.upper()}{RESET}"


def version() -> str:
    try:
        return VERSION_FILE.read_text().strip()
    except OSError:
        return "unknown"


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def human_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def host_metrics() -> tuple[str, str, str]:
    try:
        import psutil
        cpu = f"{psutil.cpu_percent(interval=0.15):.0f}%"
        vm = psutil.virtual_memory()
        ram = f"{human_bytes(vm.used)}/{human_bytes(vm.total)} ({vm.percent:.0f}%)"
        disk = psutil.disk_usage("/")
        disk_s = f"{human_bytes(disk.used)}/{human_bytes(disk.total)} ({disk.percent:.0f}%)"
        return cpu, ram, disk_s
    except Exception:
        return "n/a", "n/a", "n/a"


def panel_url(cfg: dict) -> str:
    origin = str(cfg.get("public_origin") or "").strip()
    if origin:
        return origin
    host = cfg.get("bind_host", "127.0.0.1")
    port = cfg.get("bind_port", 2087)
    return f"http://{host}:{port}"


def pause() -> None:
    try:
        input(f"\n{GRAY}Press Enter to return...{RESET}")
    except (EOFError, KeyboardInterrupt):
        pass


def confirm(message: str, token: str = "YES") -> bool:
    try:
        answer = input(f"{YELLOW}{message}{RESET}\nType {token} to continue: ").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer == token


def heading(title: str, subtitle: str = "") -> None:
    clear()
    width = 72
    print(f"{CYAN}╔{'═' * width}╗{RESET}")
    print(f"{CYAN}║{RESET}{BOLD}{'D A R K   X R A Y'.center(width)}{RESET}{CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}{PURPLE}{'CYBER CONTROL CENTER'.center(width)}{RESET}{CYAN}║{RESET}")
    print(f"{CYAN}╠{'═' * width}╣{RESET}")
    print(f"{CYAN}║{RESET} {BOLD}{title:<70}{RESET}{CYAN}║{RESET}")
    if subtitle:
        print(f"{CYAN}║{RESET} {GRAY}{subtitle:<70}{RESET}{CYAN}║{RESET}")
    print(f"{CYAN}╚{'═' * width}╝{RESET}")


def status_dashboard() -> None:
    heading("LIVE STATUS", "Panel, guard, host resources and current endpoint")
    cfg = load_config()
    panel = service_state("dark-xray.service")
    guard = service_state("dark-xray-guard.service")
    cpu, ram, disk = host_metrics()
    print(f"\n  Panel service    {badge(panel)}")
    print(f"  IP Guard         {badge(guard)}")
    print(f"  Autostart        {enabled_state('dark-xray.service')}")
    print(f"  Version          {version()}")
    print(f"  Endpoint         {panel_url(cfg)}")
    print(f"  CPU              {cpu}")
    print(f"  RAM              {ram}")
    print(f"  Disk             {disk}")
    print(f"  Hostname         {socket.gethostname()}")
    print(f"  Writes           {'enabled' if cfg.get('writes_enabled', True) else 'disabled'}")
    print(f"  Xray binary      {cfg.get('xray_binary', 'not configured')}")
    print()
    if COMMAND.exists():
        run([COMMAND, "check"])
    pause()


def service_action(action: str) -> None:
    if os.geteuid() != 0:
        print(f"{RED}Root is required for service control.{RESET}")
        pause()
        return
    if action in {"stop", "restart"}:
        if not confirm("This interrupts active proxy sessions."):
            return
    run(["systemctl", action, "dark-xray.service"])
    time.sleep(0.5)
    print("Panel:", badge(service_state("dark-xray.service")))
    pause()


def logs_menu() -> None:
    while True:
        heading("LOG CENTER", "systemd logs; no stored credentials are printed by this menu")
        print("""
  1) Last 100 panel log lines
  2) Follow panel logs live
  3) Last 100 IP Guard log lines
  4) Follow IP Guard logs live
  5) Xray/runtime diagnostics
  0) Back
""")
        c = ask()
        if c == "0":
            return
        unit = None
        follow = False
        if c in {"1", "2"}:
            unit, follow = "dark-xray.service", c == "2"
        elif c in {"3", "4"}:
            unit, follow = "dark-xray-guard.service", c == "4"
        elif c == "5":
            run([COMMAND, "doctor"])
            pause()
            continue
        else:
            continue
        args = ["journalctl", "-u", unit, "--no-pager", "-n", "100"]
        if follow:
            args = ["journalctl", "-u", unit, "-f", "-n", "30"]
            print(f"{GRAY}Ctrl+C returns to the menu.{RESET}")
        try:
            run(args)
        except KeyboardInterrupt:
            pass
        pause()


def backup_menu() -> None:
    while True:
        heading("BACKUP & RECOVERY", "Encrypted backups; live restore is intentionally not automatic")
        print("""
  1) Create encrypted backup
  2) Restore backup into a NEW destination
  3) Reset owner password
  4) Show data/config paths
  0) Back
""")
        c = ask()
        if c == "0":
            return
        if c == "1":
            default = "/var/lib/dark-xray/backups/dark-xray.darkbackup" if IS_INSTALLED else str(ROOT / "dark-xray.darkbackup")
            path = prompt("Backup output", default)
            if path:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                run([COMMAND, "backup", "--output", path])
                pause()
        elif c == "2":
            archive = prompt("Backup archive")
            destination = prompt("NEW empty restore directory", "/var/lib/dark-xray-restore")
            if archive and destination and confirm("Restore is isolated and will NOT overwrite the live instance."):
                run([COMMAND, "restore", "--archive", archive, "--destination", destination])
                pause()
        elif c == "3":
            user = prompt("Owner username", "dark")
            if user and confirm("This revokes existing sessions for that owner. TOTP remains enabled."):
                run([COMMAND, "reset-password", "--username", user])
                pause()
        elif c == "4":
            print("Config :", CONFIG)
            print("Data   :", DATA)
            print("App    :", ROOT)
            pause()


def tls_menu() -> None:
    while True:
        cfg = load_config()
        heading("TLS / DOMAIN", f"Current endpoint: {panel_url(cfg)}")
        print("""
  1) Issue / replace Let's Encrypt certificate
  2) Renew configured certificate now
  3) Show certificate details
  4) Certbot timer status
  0) Back
""")
        c = ask()
        if c == "0":
            return
        if c == "1":
            if os.geteuid() != 0:
                print(f"{RED}Root is required for certificate provisioning.{RESET}")
                pause()
                continue
            domain = prompt("Domain (no https://)")
            email = prompt("ACME email")
            port = prompt("Public panel HTTPS port", str(cfg.get("bind_port", 2087)))
            if domain and email and port and confirm("Port 80 must be reachable for HTTP-01. Continue?"):
                run([COMMAND, "domain", "--domain", domain, "--email", email, "--port", port, "--agree-tos"])
                pause()
        elif c == "2":
            if os.geteuid() == 0 and confirm("Renewal restarts DARK and may interrupt active sessions."):
                run([COMMAND, "domain", "--renew"])
                pause()
            elif os.geteuid() != 0:
                print(f"{RED}Root is required.{RESET}")
                pause()
        elif c == "3":
            cert = cfg.get("tls_certificate")
            if cert and Path(cert).exists() and command_exists("openssl"):
                run(["openssl", "x509", "-in", cert, "-noout", "-subject", "-issuer", "-dates"])
            else:
                print("No readable configured certificate.")
            pause()
        elif c == "4":
            run(["systemctl", "status", "certbot.timer", "--no-pager"])
            pause()


def guard_menu() -> None:
    while True:
        heading("IP GUARD", "Per-client source-IP policy and isolated nftables worker")
        print(f"\n  Guard service     {badge(service_state('dark-xray-guard.service'))}\n")
        print("""
  1) Guard status
  2) Configure / enable IP Guard
  3) Restart Guard worker
  4) Stop Guard worker
  5) View Guard logs
  6) Show enablement safety notes
  0) Back
""")
        c = ask()
        if c == "0":
            return
        if c == "1":
            run(["systemctl", "status", "dark-xray-guard.service", "--no-pager"])
            pause()
        elif c == "2":
            if os.geteuid() != 0:
                print(f"{RED}Root is required.{RESET}")
                pause()
                continue
            ports = prompt("Explicit Xray DATA ports (comma separated, e.g. 443,2020)")
            if not ports:
                continue
            exempt = prompt("Optional exempt IP (blank = none)")
            print(f"{YELLOW}Only continue if Xray logs show the real client packet source on THIS host.{RESET}")
            if confirm("I verified direct source IPs. Enable reviewed firewall enforcement?"):
                args = [COMMAND, "guard-enable", "--ports", ports, "--verified-direct-sources"]
                if exempt:
                    args += ["--exempt", exempt]
                run(args)
                pause()
        elif c == "3":
            if os.geteuid() == 0:
                run(["systemctl", "restart", "dark-xray-guard.service"])
            else:
                print(f"{RED}Root is required.{RESET}")
            pause()
        elif c == "4":
            if os.geteuid() == 0 and confirm("Temporary DARK bans are cleared when the broker stops."):
                run(["systemctl", "stop", "dark-xray-guard.service"])
            pause()
        elif c == "5":
            run(["journalctl", "-u", "dark-xray-guard.service", "--no-pager", "-n", "120"])
            pause()
        elif c == "6":
            print("""
  • Do NOT enable on an opaque IP tunnel/proxy path.
  • SSH, panel and Xray API ports remain protected.
  • The worker owns a DARK-only nftables table; it does not flush the host ruleset.
  • One public IP may represent several devices behind NAT.
  • Validate with two independent source networks before production use.
""")
            pause()


def system_menu() -> None:
    while True:
        heading("SYSTEM & NETWORK", "Host-side helpers; changes require explicit confirmation")
        print("""
  1) Full diagnostics
  2) Enable BBR (fq + bbr)
  3) Show firewall / nftables summary
  4) Show listening ports
  5) Enable panel autostart
  6) Disable panel autostart
  0) Back
""")
        c = ask()
        if c == "0":
            return
        if c == "1":
            run([COMMAND, "doctor"])
            pause()
        elif c == "2":
            if os.geteuid() != 0:
                print(f"{RED}Root is required.{RESET}")
            elif confirm("Set net.core.default_qdisc=fq and net.ipv4.tcp_congestion_control=bbr?"):
                Path("/etc/sysctl.d/99-dark-xray-bbr.conf").write_text(
                    "net.core.default_qdisc=fq\nnet.ipv4.tcp_congestion_control=bbr\n"
                )
                run(["sysctl", "--system"])
            pause()
        elif c == "3":
            if command_exists("nft"):
                run(["nft", "list", "tables"])
                run(["nft", "list", "table", "inet", "dark_xray"])
            else:
                print("nft command is not installed.")
            pause()
        elif c == "4":
            run(["ss", "-lntup"])
            pause()
        elif c in {"5", "6"}:
            if os.geteuid() != 0:
                print(f"{RED}Root is required.{RESET}")
            else:
                run(["systemctl", "enable" if c == "5" else "disable", "dark-xray.service"])
            pause()


def settings_view() -> None:
    heading("CURRENT SETTINGS", "Secrets and private keys are intentionally hidden")
    cfg = load_config()
    safe_keys = (
        "public_origin", "public_address", "bind_host", "bind_port",
        "xray_binary", "xray_assets", "xray_api_port", "core_autostart",
        "writes_enabled", "secure_cookie", "protected_ports",
        "direct_source_verified",
    )
    for key in safe_keys:
        if key in cfg:
            print(f"  {key:<24} {cfg[key]}")
    for key in ("tls_certificate", "tls_private_key"):
        if cfg.get(key):
            print(f"  {key:<24} configured")
    pause()


def ask() -> str:
    try:
        return input(f"{CYAN}DARK{RESET}{PURPLE}XRAY{RESET} > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "0"


def prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{label}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    return value or default


def main_menu() -> None:
    while True:
        cfg = load_config()
        panel = service_state("dark-xray.service")
        guard = service_state("dark-xray-guard.service")
        cpu, ram, _ = host_metrics()
        heading(
            f"DARK XRAY {version()}",
            f"Panel {panel.upper()}  •  Guard {guard.upper()}  •  CPU {cpu}  •  RAM {ram}",
        )
        print(f"""
 {GREEN}SERVICE{RESET}                              {PURPLE}SECURITY / NETWORK{RESET}
  1) Live status dashboard                  8) TLS / Domain manager
  2) Start DARK XRAY                        9) IP Guard manager
  3) Stop DARK XRAY                        10) System / Network tools
  4) Restart DARK XRAY + Xray

 {BLUE}MAINTENANCE{RESET}                          {CYAN}INFORMATION{RESET}
  5) Logs & diagnostics                    11) Current safe settings
  6) Backup & recovery                     12) Open panel address
  7) Run doctor                            13) About / version

  0) Exit
""")
        c = ask()
        if c in {"0", "q", "quit", "exit"}:
            clear()
            print(f"{CYAN}DARK XRAY{RESET} control center closed.")
            return
        if c == "1":
            status_dashboard()
        elif c == "2":
            service_action("start")
        elif c == "3":
            service_action("stop")
        elif c == "4":
            service_action("restart")
        elif c == "5":
            logs_menu()
        elif c == "6":
            backup_menu()
        elif c == "7":
            run([COMMAND, "doctor"])
            pause()
        elif c == "8":
            tls_menu()
        elif c == "9":
            guard_menu()
        elif c == "10":
            system_menu()
        elif c == "11":
            settings_view()
        elif c == "12":
            print(f"\nPanel: {panel_url(cfg)}")
            if str(cfg.get("bind_host", "")).startswith("127.") and IS_INSTALLED:
                print("Loopback mode: use SSH port forwarding or configure TLS/domain first.")
            pause()
        elif c == "13":
            heading("ABOUT")
            print(f"\n  DARK XRAY {version()}")
            print("  Standalone Xray control panel")
            print("  Command: darkxray")
            print("  Repository: https://github.com/darktunnelmika/dark-xray")
            print("\n  This menu never displays stored passwords, API tokens or private keys.")
            pause()


if __name__ == "__main__":
    main_menu()
