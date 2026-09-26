"""Cloudflare WARP registration helpers for DARK XRAY.

The public API is fixed to Cloudflare's official WARP registration endpoint.
Secrets are returned only to the caller that builds the Xray outbound and are
never exposed by status endpoints.
"""
from __future__ import annotations

import base64
import ipaddress
import socket
import time
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

WARP_API_BASE = "https://api.cloudflareclient.com/v0a4005"
WARP_CLIENT_VERSION = "a-6.30-3596"

WARP_CONSUMER_V4 = ipaddress.ip_network("162.159.192.0/24")
WARP_PORTS = (2408, 500, 1701, 4500)
WARP_SAMPLE_HOSTS = (1, 5, 10, 50, 100, 150, 200, 250)


def warp_endpoint_candidates(current: str = "") -> list[str]:
    """Return a compact, deterministic set of consumer WARP WireGuard paths."""
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
        raise WarpRegistrationError("Invalid WARP endpoint")
    if raw.startswith("["):
        raise WarpRegistrationError("IPv6 WARP endpoint selection is not enabled in the compact scanner")
    if ":" not in raw:
        raise WarpRegistrationError("WARP endpoint requires a UDP port")
    host, port_raw = raw.rsplit(":", 1)
    try:
        port = int(port_raw)
    except ValueError as ex:
        raise WarpRegistrationError("Invalid WARP endpoint port") from ex
    if port not in WARP_PORTS:
        raise WarpRegistrationError("Unsupported WARP endpoint port")
    if host == "engage.cloudflareclient.com":
        return f"{host}:{port}"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as ex:
        raise WarpRegistrationError("WARP endpoint must be the Cloudflare consumer hostname or IPv4 ingress") from ex
    if ip.version != 4 or ip not in WARP_CONSUMER_V4:
        raise WarpRegistrationError("WARP endpoint is outside the Cloudflare consumer WARP range")
    return f"{ip}:{port}"


class WarpRegistrationError(RuntimeError):
    pass


def generate_wireguard_keypair() -> tuple[str, str]:
    key = X25519PrivateKey.generate()
    private_raw = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_raw = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(private_raw).decode("ascii"),
        base64.b64encode(public_raw).decode("ascii"),
    )


def _reserved(client_id: str) -> list[int]:
    if not client_id:
        return []
    try:
        raw = base64.b64decode(client_id + "=" * ((4 - len(client_id) % 4) % 4), validate=True)
    except Exception as ex:
        raise WarpRegistrationError("WARP registration returned an invalid client_id") from ex
    return list(raw)
def build_warp_outbound(payload: dict[str, Any], private_key: str, *, tag: str = "warp") -> dict[str, Any]:
    try:
        config = payload["config"]
        interface = config["interface"]
        addresses = interface["addresses"]
        peer = config["peers"][0]
        endpoint = peer["endpoint"]
        endpoint_host = endpoint["host"] if isinstance(endpoint, dict) else str(endpoint)
        peer_key = str(peer["public_key"])
    except (KeyError, IndexError, TypeError) as ex:
        raise WarpRegistrationError("WARP registration response is incomplete") from ex

    if not endpoint_host or not peer_key:
        raise WarpRegistrationError("WARP registration returned an empty peer")
    if ":" not in endpoint_host.rsplit("]", 1)[-1]:
        ports = endpoint.get("ports") if isinstance(endpoint, dict) else None
        port = int(ports[0]) if isinstance(ports, list) and ports else 2408
        endpoint_host = f"{endpoint_host}:{port}"

    address: list[str] = []
    v4 = str(addresses.get("v4") or "").strip()
    v6 = str(addresses.get("v6") or "").strip()
    if v4:
        address.append(v4 if "/" in v4 else v4 + "/32")
    if v6:
        address.append(v6 if "/" in v6 else v6 + "/128")
    if not address:
        raise WarpRegistrationError("WARP registration returned no interface address")

    reserved = _reserved(str(config.get("client_id") or ""))
    settings: dict[str, Any] = {
        "secretKey": private_key,
        "address": address,
        "mtu": 1280,
        "peers": [{
            "publicKey": peer_key,
            "endpoint": endpoint_host,
            "allowedIPs": ["0.0.0.0/0", "::/0"],
            "keepAlive": 30,
        }],
    }
    if reserved:
        settings["reserved"] = reserved
    return {"tag": tag, "protocol": "wireguard", "settings": settings, "streamSettings": {"sockopt": {}}}


def register_cloudflare_warp(*, tag: str = "warp", client: httpx.Client | None = None) -> dict[str, Any]:
    private_key, public_key = generate_wireguard_keypair()
    body = {
        "key": public_key,
        "tos": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "type": "PC",
        "model": "dark-xray",
        "name": socket.gethostname()[:63] or "dark-xray",
    }
    own = client is None
    if client is None:
        client = httpx.Client(timeout=15.0, follow_redirects=False)
    try:
        response = client.post(
            WARP_API_BASE + "/reg",
            headers={"CF-Client-Version": WARP_CLIENT_VERSION, "Content-Type": "application/json"},
            json=body,
        )
        if response.status_code < 200 or response.status_code >= 300:
            detail = response.text[:400].replace("\n", " ")
            raise WarpRegistrationError(f"WARP registration failed (HTTP {response.status_code}): {detail}")
        if len(response.content) > 1024 * 1024:
            raise WarpRegistrationError("WARP registration response is too large")
        try:
            payload = response.json()
        except ValueError as ex:
            raise WarpRegistrationError("WARP registration returned invalid JSON") from ex
        if not isinstance(payload, dict) or not payload.get("id") or not payload.get("token"):
            raise WarpRegistrationError("WARP registration response is missing device identity")
        outbound = build_warp_outbound(payload, private_key, tag=tag)
        return {
            "outbound": outbound,
            "deviceId": str(payload["id"]),
            "registered": True,
        }
    except httpx.HTTPError as ex:
        raise WarpRegistrationError("Could not reach Cloudflare WARP registration service") from ex
    finally:
        if own:
            client.close()
