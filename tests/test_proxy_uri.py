"""
测试 proxy_uri.py 的 URI 解析与导出。
运行: python -m pytest tests/ -v
"""
import sys
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent / "app"
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

import pytest
from proxy_uri import (
    is_remote_subscription_url,
    looks_like_proxy_uri_line,
    parse_single_proxy_uri,
    proxy_dict_to_uri,
)


class TestIsRemoteSubscriptionUrl:
    def test_http(self):
        assert is_remote_subscription_url("http://example.com/sub") is True

    def test_https(self):
        assert is_remote_subscription_url("https://example.com/sub") is True

    def test_ss_not_remote(self):
        assert is_remote_subscription_url("ss://YWVzLTI1Ni1nY206cGFzc3dvcmQ=@1.2.3.4:8388") is False

    def test_empty(self):
        assert is_remote_subscription_url("") is False


class TestLooksLikeProxyUriLine:
    @pytest.mark.parametrize("uri", [
        "ss://YWVzLTI1Ni1nY206cGFzc3dvcmQ=@1.2.3.4:8388",
        "vmess://eyJhZGQiOiIxLjIuMy40IiwicG9ydCI6IjQ0MyJ9",
        "vless://uuid@1.2.3.4:443",
        "trojan://password@1.2.3.4:443",
        "hysteria2://password@1.2.3.4:443",
        "hy2://password@1.2.3.4:443",
    ])
    def test_recognized(self, uri):
        assert looks_like_proxy_uri_line(uri) is True

    def test_plain_text_not_recognized(self):
        assert looks_like_proxy_uri_line("not a uri") is False

    def test_http_not_recognized(self):
        assert looks_like_proxy_uri_line("https://example.com") is False


class TestParseSsUri:
    def test_basic_ss(self):
        # Base64-encoded "aes-256-gcm:password"
        import base64
        userinfo = base64.urlsafe_b64encode(b"aes-256-gcm:password").decode().rstrip("=")
        uri = f"ss://{userinfo}@192.168.1.1:8388#TestNode"
        p = parse_single_proxy_uri(uri)
        assert p is not None
        assert p["type"] == "ss"
        assert p["server"] == "192.168.1.1"
        assert p["port"] == 8388
        assert p["cipher"] == "aes-256-gcm"
        assert p["password"] == "password"

    def test_ss_sip002(self):
        import base64
        userinfo = base64.urlsafe_b64encode(b"chacha20-ietf-poly1305:mypass").decode().rstrip("=")
        uri = f"ss://{userinfo}@10.0.0.1:1080"
        p = parse_single_proxy_uri(uri)
        assert p is not None
        assert p["type"] == "ss"
        assert p["cipher"] == "chacha20-ietf-poly1305"


class TestParseVmessUri:
    def test_basic_vmess(self):
        import base64, json
        payload = {
            "v": "2", "ps": "TestVmess", "add": "1.2.3.4",
            "port": "443", "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "aid": "0", "net": "tcp", "type": "none",
            "tls": "tls", "host": "", "path": "",
        }
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        p = parse_single_proxy_uri(f"vmess://{encoded}")
        assert p is not None
        assert p["type"] == "vmess"
        assert p["server"] == "1.2.3.4"
        assert p["port"] == 443
        assert p.get("name") == "TestVmess"


class TestParseTrojanUri:
    def test_basic_trojan(self):
        p = parse_single_proxy_uri("trojan://mypassword@remote.host:443?sni=example.com#MyTrojan")
        assert p is not None
        assert p["type"] == "trojan"
        assert p["server"] == "remote.host"
        assert p["port"] == 443
        assert p["password"] == "mypassword"

    def test_name_from_fragment(self):
        p = parse_single_proxy_uri("trojan://pass@1.2.3.4:443#MyNode")
        assert p is not None
        assert p.get("name") == "MyNode"


class TestParseVlessUri:
    def test_basic_vless(self):
        uuid = "12345678-1234-1234-1234-123456789012"
        uri = f"vless://{uuid}@1.2.3.4:443?type=tcp#VlessNode"
        p = parse_single_proxy_uri(uri)
        assert p is not None
        assert p["type"] == "vless"
        assert p["uuid"] == uuid
        assert p["server"] == "1.2.3.4"


class TestParseHysteria2Uri:
    def test_hy2_alias(self):
        p = parse_single_proxy_uri("hy2://password@1.2.3.4:443#Hy2Node")
        assert p is not None
        assert p["type"] == "hysteria2"

    def test_hysteria2_prefix(self):
        p = parse_single_proxy_uri("hysteria2://password@1.2.3.4:443")
        assert p is not None
        assert p["type"] == "hysteria2"


class TestProxyDictToUri:
    def test_trojan_roundtrip(self):
        p = parse_single_proxy_uri("trojan://pass@1.2.3.4:443#Node")
        assert p is not None
        uri = proxy_dict_to_uri(p)
        assert uri is not None
        assert uri.startswith("trojan://")

    def test_hysteria_clash_to_uri(self):
        p = {
            "name": "Hy1",
            "type": "hysteria",
            "server": "1.2.3.4",
            "port": 443,
            "auth_str": "secret",
            "protocol": "udp",
            "sni": "ex.com",
            "up": "80 Mbps",
            "down": "100 Mbps",
        }
        uri = proxy_dict_to_uri(p)
        assert uri is not None
        assert uri.startswith("hysteria://")
        assert "auth=secret" in uri
        assert "peer=ex.com" in uri

    def test_hy2_alias_to_hysteria2_uri(self):
        p = {
            "name": "N",
            "type": "hy2",
            "server": "1.2.3.4",
            "port": 8443,
            "password": "p",
        }
        uri = proxy_dict_to_uri(p)
        assert uri is not None
        assert uri.startswith("hysteria2://")

    def test_vless_reality_roundtrip_keeps_reality_params(self):
        src = (
            "vless://11111111-2222-3333-4444-555555555555@example.com:443"
            "?encryption=none&flow=xtls-rprx-vision&security=reality"
            "&sni=www.example.com&fp=chrome"
            "&pbk=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            "&sid=abcd1234&type=tcp&headerType=none#vless-reality-test"
        )
        parsed = parse_single_proxy_uri(src)
        assert parsed is not None
        out = proxy_dict_to_uri(parsed)
        assert out is not None
        assert "security=reality" in out
        assert "pbk=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" in out
        assert "sid=abcd1234" in out
        assert "flow=xtls-rprx-vision" in out

    def test_unsupported_type_returns_none(self):
        p = {"name": "test", "type": "wireguard", "server": "1.2.3.4", "port": 51820}
        uri = proxy_dict_to_uri(p)
        assert uri is None
