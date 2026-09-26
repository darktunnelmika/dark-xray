"""Isolated outbound latency probes for DARK XRAY.

A single temporary Xray process is used for a batch. Each tested outbound gets
its own loopback HTTP inbound and routing rule. Production Xray and customer
traffic are never touched.
"""
from __future__ import annotations

import concurrent.futures
import copy
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROBE_URL = "https://www.gstatic.com/generate_204"
TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
MAX_TARGETS = 32
MAX_ATTEMPTS = 3
UNTESTABLE = {"freedom", "dns", "blackhole", "loopback"}


class OutboundProbeError(RuntimeError):
    pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _wait_port(port: int, process: subprocess.Popen, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise OutboundProbeError("temporary Xray exited during startup")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.12):
                return
        except OSError:
            time.sleep(.06)
    raise OutboundProbeError("temporary Xray probe did not become ready")
def _request(port: int, url: str, timeout: float) -> tuple[float, int, str]:
    proxy = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "DARK-XRAY-OUTBOUND-PROBE/1"})
    started = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout) as response:
            status = int(getattr(response, "status", response.getcode()))
            body = response.read(4096).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as ex:
        raise OutboundProbeError(type(ex).__name__) from ex
    if not 200 <= status < 400:
        raise OutboundProbeError(f"HTTP {status}")
    return (time.perf_counter() - started) * 1000.0, status, body


def _trace(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in body.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"ip", "loc", "colo", "warp"}:
            out[key] = value[:80]
    return out


def _validate_xray(binary: str, assets: str, path: Path) -> None:
    env = os.environ.copy()
    if assets:
        env["XRAY_LOCATION_ASSET"] = assets
    try:
        cp = subprocess.run(
            [binary, "run", "-test", "-config", str(path)],
            env=env, capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as ex:
        raise OutboundProbeError("temporary Xray validation failed") from ex
    if cp.returncode:
        detail = (cp.stderr or cp.stdout or "Xray rejected probe config").strip()[-300:]
        raise OutboundProbeError(detail)


def _testable(outbound: dict[str, Any]) -> bool:
    protocol = str(outbound.get("protocol") or "").lower()
    return bool(outbound.get("tag")) and protocol not in UNTESTABLE
def probe_outbounds(
    binary: str,
    assets: str,
    outbounds: list[dict[str, Any]],
    *,
    tags: list[str] | None = None,
    attempts: int = 1,
    timeout: float = 5.0,
    trace: bool = False,
) -> list[dict[str, Any]]:
    if type(attempts) is not int or not 1 <= attempts <= MAX_ATTEMPTS:
        raise OutboundProbeError("attempts must be between 1 and 3")
    if not 1.0 <= float(timeout) <= 10.0:
        raise OutboundProbeError("timeout must be between 1 and 10 seconds")
    if not isinstance(outbounds, list):
        raise OutboundProbeError("outbounds must be a list")

    by_tag = {str(o.get("tag")): o for o in outbounds if isinstance(o, dict) and o.get("tag")}
    selected = list(dict.fromkeys(tags or list(by_tag)))
    if len(selected) > MAX_TARGETS:
        raise OutboundProbeError(f"at most {MAX_TARGETS} outbounds can be tested")
    missing = [tag for tag in selected if tag not in by_tag]
    if missing:
        raise OutboundProbeError("unknown outbound tag(s): " + ", ".join(missing))

    results: dict[str, dict[str, Any]] = {}
    targets: list[tuple[str, int]] = []
    for tag in selected:
        outbound = by_tag[tag]
        if not _testable(outbound):
            results[tag] = {
                "tag": tag, "testable": False, "success": False, "delayMs": None,
                "lossPercent": None, "jitterMs": None, "error": "Not testable",
                "productionTrafficMutation": False,
            }
            continue
        targets.append((tag, _free_port()))

    if not targets:
        return [results[tag] for tag in selected]

    inbounds = [{
        "tag": f"dark-probe-in-{idx}", "listen": "127.0.0.1", "port": port,
        "protocol": "http", "settings": {},
    } for idx, (_tag, port) in enumerate(targets)]
    rules = [{
        "type": "field", "inboundTag": [f"dark-probe-in-{idx}"], "outboundTag": tag,
    } for idx, (tag, _port) in enumerate(targets)]
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": inbounds,
        "outbounds": copy.deepcopy(outbounds),
        "routing": {"domainStrategy": "AsIs", "rules": rules},
    }
    process: subprocess.Popen | None = None
    with tempfile.TemporaryDirectory(prefix="dark-outbound-probe-") as td:
        path = Path(td) / "config.json"
        path.write_text(json.dumps(config, separators=(",", ":")), encoding="utf-8")
        os.chmod(path, 0o600)
        _validate_xray(binary, assets, path)
        env = os.environ.copy()
        if assets:
            env["XRAY_LOCATION_ASSET"] = assets
        try:
            process = subprocess.Popen(
                [binary, "run", "-config", str(path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=env, cwd=td, start_new_session=True,
            )
            for _tag, port in targets:
                _wait_port(port, process)

            urls = [TRACE_URL]

            def run_one(item: tuple[str, int]) -> tuple[str, dict[str, Any]]:
                tag, port = item
                latencies: list[float] = []
                errors: list[str] = []
                trace_data: dict[str, str] = {}
                for _ in range(attempts):
                    success = False
                    for url in urls:
                        try:
                            delay, _status, body = _request(port, url, float(timeout))
                            latencies.append(delay)
                            if trace or url == TRACE_URL:
                                trace_data = _trace(body)
                            success = True
                            break
                        except OutboundProbeError as ex:
                            errors.append(str(ex)[:120])
                    if not success:
                        continue
                lost = attempts - len(latencies)
                avg = round(sum(latencies) / len(latencies), 3) if latencies else None
                jitter = round(max(latencies) - min(latencies), 3) if len(latencies) > 1 else 0.0 if latencies else None
                row: dict[str, Any] = {
                    "tag": tag, "testable": True, "success": bool(latencies),
                    "delayMs": avg, "lossPercent": round(lost * 100.0 / attempts, 2),
                    "jitterMs": jitter,
                    "error": "" if latencies else (errors[-1] if errors else "probe failed"),
                    "productionTrafficMutation": False,
                }
                if trace:
                    row["egress"] = {
                        "ip": trace_data.get("ip", ""),
                        "country": trace_data.get("loc", ""),
                        "colo": trace_data.get("colo", ""),
                        "warp": trace_data.get("warp", ""),
                    }
                    row["warpVerified"] = trace_data.get("warp", "").lower() in {"on", "plus"}
                return tag, row

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(targets))) as pool:
                for tag, row in pool.map(run_one, targets):
                    results[tag] = row
        finally:
            _stop(process)

    return [results[tag] for tag in selected]
