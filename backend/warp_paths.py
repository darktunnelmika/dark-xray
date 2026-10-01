"""Dependency-light Cloudflare WARP endpoint helpers shared by Hub and Node."""
from __future__ import annotations

import ipaddress

WARP_CONSUMER_V4 = ipaddress.ip_network("162.159.192.0/24")
WARP_PORTS = (2408, 500, 1701, 4500)
WARP_SAMPLE_HOSTS = (1, 5, 10, 50, 100, 150, 200, 250)


class WarpPathError(RuntimeError):
    pass


def warp_endpoint_candidates(current: str = "") -> list[str]:
    items: list[str] = []
    if current:
        items.append(current)
    items.append("engage.cloudflareclient.com:2408")
    for last in WARP_SAMPLE_HOSTS:
        items.append(f"162.159.192.{last}:2408")
    for last in (5, 200):
        for port in WARP_PORTS[1:]:
            items.append(f"162.159.192.{last}:{port}")
    return list(dict.fromkeys(items))


def validate_warp_endpoint(value: str) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > 160:
        raise WarpPathError("Invalid WARP endpoint")
    if raw.startswith("["):
        raise WarpPathError("IPv6 WARP endpoint selection is not enabled in the compact scanner")
    if ":" not in raw:
        raise WarpPathError("WARP endpoint requires a UDP port")
    host, port_raw = raw.rsplit(":", 1)
    try:
        port = int(port_raw)
    except ValueError as ex:
        raise WarpPathError("Invalid WARP endpoint port") from ex
    if port not in WARP_PORTS:
        raise WarpPathError("Unsupported WARP endpoint port")
    if host == "engage.cloudflareclient.com":
        return f"{host}:{port}"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as ex:
        raise WarpPathError("WARP endpoint must be the Cloudflare consumer hostname or IPv4 ingress") from ex
    if ip.version != 4 or ip not in WARP_CONSUMER_V4:
        raise WarpPathError("WARP endpoint is outside the Cloudflare consumer WARP range")
    return f"{ip}:{port}"


def warp_scan_results(endpoints: list[str], raw: list[dict], active: str = "", candidate: str = "") -> list[dict]:
    """One honest result per requested endpoint; never infer health/location from IP.

    Latency is HTTP-through-WARP latency, not ICMP. Partial success, loss, high
    latency or jitter is degraded. An unverified WARP egress is always failed.
    """
    import math

    def metric(value):
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (ValueError, TypeError):
            return None
        return number if math.isfinite(number) and number >= 0 else None

    by_endpoint = {str(row.get("endpoint", "")): row for row in raw if isinstance(row, dict)}
    rows = []
    for endpoint in dict.fromkeys(endpoints):
        row = by_endpoint.get(endpoint, {})
        verified = bool(row.get("success")) and bool(row.get("warpVerified"))
        delay, loss, jitter = (metric(row.get(key)) for key in ("delayMs", "lossPercent", "jitterMs"))
        ready = verified and delay is not None
        status = "failed" if not ready else "degraded" if (
            loss is None or loss > 0 or delay > 500 or jitter is None or jitter > 100
        ) else "healthy"
        egress = row.get("egress") if ready and isinstance(row.get("egress"), dict) else {}
        error = str(row.get("error") or ("No result returned for endpoint" if not row else
                    "WARP egress was not verified" if not ready else ""))[:300]
        rows.append({"endpoint": endpoint, "ready": ready, "status": status,
                     "selected": bool(active and endpoint == active),
                     "candidateDefault": bool(candidate and endpoint == candidate),
                     "delayMs": delay, "lossPercent": loss, "jitterMs": jitter,
                     "country": str(egress.get("country", "")), "colo": str(egress.get("colo", "")),
                     "egressIp": str(egress.get("ip", "")), "warp": str(egress.get("warp", "")),
                     "latencyKind": "http-through-warp", "error": error})
    order = {"healthy": 0, "degraded": 1, "failed": 2}
    rows.sort(key=lambda row: (order[row["status"]], row["lossPercent"] if row["lossPercent"] is not None else 100,
                              row["delayMs"] if row["delayMs"] is not None else float("inf")))
    return rows
