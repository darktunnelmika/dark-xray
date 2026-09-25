import base64

import pytest

from warp_cloudflare import WarpRegistrationError, build_warp_outbound, register_cloudflare_warp


def payload():
    return {
        "id": "device-1",
        "token": "token-1",
        "config": {
            "client_id": base64.b64encode(bytes([1, 2, 3])).decode(),
            "interface": {"addresses": {"v4": "172.16.0.2", "v6": "2606:4700:110:8::2"}},
            "peers": [{
                "public_key": "peer-public-key",
                "endpoint": {"host": "162.159.192.1:2408"},
            }],
        },
    }


def test_build_warp_outbound_maps_cloudflare_profile_to_xray():
    out = build_warp_outbound(payload(), "private-secret", tag="warp")
    assert out["tag"] == "warp"
    assert out["protocol"] == "wireguard"
    settings = out["settings"]
    assert settings["secretKey"] == "private-secret"
    assert settings["address"] == ["172.16.0.2/32", "2606:4700:110:8::2/128"]
    assert settings["reserved"] == [1, 2, 3]
    assert settings["peers"][0]["publicKey"] == "peer-public-key"
    assert settings["peers"][0]["endpoint"] == "162.159.192.1:2408"


class Response:
    status_code = 200
    content = b"{}"
    text = "{}"

    def json(self):
        return payload()


class Client:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response()


def test_register_warp_uses_cloudflare_api_and_returns_no_api_token():
    client = Client()
    result = register_cloudflare_warp(client=client)
    assert result["registered"] is True
    assert result["deviceId"] == "device-1"
    assert "token" not in result and "access_token" not in result
    assert result["outbound"]["protocol"] == "wireguard"
    url, kwargs = client.calls[0]
    assert url.endswith("/reg")
    assert kwargs["headers"]["CF-Client-Version"]
    assert kwargs["json"]["model"] == "dark-xray"


def test_incomplete_warp_response_is_rejected():
    bad = payload()
    bad["config"]["peers"] = []
    with pytest.raises(WarpRegistrationError, match="incomplete"):
        build_warp_outbound(bad, "private-secret")
