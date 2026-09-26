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
