#!/usr/bin/env python3
"""Isolated real-path WARP probe for DARK XRAY Stage 7.

A probe starts a temporary Xray process bound only to 127.0.0.1, routes one
local HTTP proxy inbound through one existing WireGuard outbound, performs a
small HTTPS request through that proxy, then terminates the temporary child.

It never edits DARK settings, never restarts the production Xray child, and
never returns WireGuard keys or peer configuration.
"""
from __future__ import annotations

import copy
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class SmartWarpProbeError(RuntimeError):
    pass


PROBE_URL = "https://www.gstatic.com/generate_204"
MAX_CANDIDATES = 8
MAX_ATTEMPTS = 3
_SCAN_LOCK = threading.Lock()


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_port(port: int, process: subprocess.Popen, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SmartWarpProbeError("temporary Xray exited during startup")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.15):
                return
        except OSError:
            time.sleep(0.08)
    raise SmartWarpProbeError("temporary Xray proxy did not become ready")


def _stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _validate_candidate(outbound: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(outbound, dict):
        raise SmartWarpProbeError("WARP candidate must be an outbound object")
    tag = str(outbound.get("tag") or "")
    if not tag or len(tag) > 128:
        raise SmartWarpProbeError("WARP candidate requires a valid outbound tag")
    if str(outbound.get("protocol") or "").lower() != "wireguard":
        raise SmartWarpProbeError("real WARP scan currently supports WireGuard outbounds only")
    stream = outbound.get("streamSettings", {})
    if stream is not None and not isinstance(stream, dict):
        raise SmartWarpProbeError("invalid WireGuard outbound stream settings")
    sock = (stream or {}).get("sockopt", {})
    if sock is not None and not isinstance(sock, dict):
        raise SmartWarpProbeError("invalid WireGuard outbound socket settings")
    if (sock or {}).get("dialerProxy"):
        raise SmartWarpProbeError("chained WireGuard outbounds are not scanned in Stage 7")
    settings = outbound.get("settings")
    if not isinstance(settings, dict):
        raise SmartWarpProbeError("WireGuard outbound settings are missing")
    if not isinstance(settings.get("secretKey"), str) or not settings.get("secretKey"):
        raise SmartWarpProbeError("WireGuard outbound has no secretKey")
    peers = settings.get("peers")
    if not isinstance(peers, list) or not peers or not isinstance(peers[0], dict):
        raise SmartWarpProbeError("WireGuard outbound has no peer")
    clean = copy.deepcopy(outbound)
    clean["tag"] = "dark-warp-probe-out"
    return tag, clean


def _probe_config(outbound: dict[str, Any], port: int) -> dict[str, Any]:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "tag": "dark-warp-probe-in",
            "listen": "127.0.0.1",
            "port": port,
            "protocol": "http",
            "settings": {},
        }],
        "outbounds": [outbound],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [{
                "type": "field",
                "inboundTag": ["dark-warp-probe-in"],
                "outboundTag": "dark-warp-probe-out",
            }],
        },
    }


def _validate_xray(binary: str, assets: str, path: Path) -> None:
    env = os.environ.copy()
    if assets:
        env["XRAY_LOCATION_ASSET"] = assets
    try:
        cp = subprocess.run(
            [binary, "run", "-test", "-config", str(path)],
            env=env, capture_output=True, text=True, timeout=12, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as ex:
        raise SmartWarpProbeError("temporary Xray validation could not run") from ex
    if cp.returncode:
        raise SmartWarpProbeError("Xray rejected the selected WARP outbound")


def _https_probe(proxy_port: int, timeout: float) -> float:
    proxy = f"http://127.0.0.1:{proxy_port}"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )
    request = urllib.request.Request(
        PROBE_URL,
        method="GET",
        headers={"User-Agent": "DARK-XRAY-WARP-PROBE/1"},
    )
    started = time.perf_counter()
    try:
        with opener.open(request, timeout=timeout) as response:
            # gstatic generate_204 normally returns 204. A small successful 2xx/3xx
            # still proves the selected outbound reached the Internet.
            status = int(getattr(response, "status", response.getcode()))
            response.read(1024)
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        raise SmartWarpProbeError(type(ex).__name__) from ex
    if not 200 <= status < 400:
        raise SmartWarpProbeError("unexpected HTTP status")
    return (time.perf_counter() - started) * 1000.0


def scan_warp_outbound(
    binary: str,
    assets: str,
    outbound: dict[str, Any],
    *,
    attempts: int = 3,
    timeout: float = 5.0,
) -> dict[str, Any]:
    if type(attempts) is not int or not 1 <= attempts <= MAX_ATTEMPTS:
        raise SmartWarpProbeError("attempts must be between 1 and 3")
    if not 1.0 <= float(timeout) <= 10.0:
        raise SmartWarpProbeError("probe timeout must be between 1 and 10 seconds")

    source_tag, candidate = _validate_candidate(outbound)
    port = _free_loopback_port()
    cfg = _probe_config(candidate, port)
    process: subprocess.Popen | None = None
    latencies: list[float] = []
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="dark-warp-probe-") as td:
        path = Path(td) / "config.json"
        path.write_text(json.dumps(cfg, separators=(",", ":")), encoding="utf-8")
        os.chmod(path, 0o600)
        _validate_xray(binary, assets, path)
        env = os.environ.copy()
        if assets:
            env["XRAY_LOCATION_ASSET"] = assets
        try:
            process = subprocess.Popen(
                [binary, "run", "-config", str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                cwd=td,
                start_new_session=True,
            )
            _wait_port(port, process)
            for _ in range(attempts):
                try:
                    latencies.append(_https_probe(port, float(timeout)))
                except SmartWarpProbeError as ex:
                    failures.append(str(ex)[:80])
        finally:
            _stop(process)

    lost = attempts - len(latencies)
    return {
        "tag": source_tag,
        "ok": bool(latencies),
        "latenciesMs": [round(x, 3) for x in latencies],
        "lossPercent": round((lost / attempts) * 100.0, 2),
        "attempts": attempts,
        "successes": len(latencies),
        "failures": lost,
        "error": "" if latencies else (failures[-1] if failures else "probe failed"),
        "source": "isolated-temporary-xray-http-proxy",
        "probeUrl": PROBE_URL,
        "productionTrafficMutation": False,
    }


def scan_warp_outbounds(
    binary: str,
    assets: str,
    outbounds: list[dict[str, Any]],
    *,
    attempts: int = 3,
    timeout: float = 5.0,
) -> list[dict[str, Any]]:
    if not isinstance(outbounds, list) or not outbounds:
        raise SmartWarpProbeError("select at least one WARP outbound")
    if len(outbounds) > MAX_CANDIDATES:
        raise SmartWarpProbeError("at most 8 WARP outbounds can be scanned at once")
    results: list[dict[str, Any]] = []
    # Sequential on purpose: low burst, predictable CPU/RAM use on 1 vCPU nodes.
    for outbound in outbounds:
        try:
            results.append(scan_warp_outbound(
                binary, assets, outbound, attempts=attempts, timeout=timeout
            ))
        except SmartWarpProbeError as ex:
            tag = str(outbound.get("tag") or "") if isinstance(outbound, dict) else ""
            results.append({
                "tag": tag,
                "ok": False,
                "latenciesMs": [],
                "lossPercent": 100.0,
                "attempts": attempts,
                "successes": 0,
                "failures": attempts,
                "error": str(ex)[:120],
                "source": "isolated-temporary-xray-http-proxy",
                "probeUrl": PROBE_URL,
                "productionTrafficMutation": False,
            })
    return results