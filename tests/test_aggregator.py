"""
测试 aggregator.py 的解析、过滤、重命名逻辑。
"""
import sys
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent / "app"
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

import base64

import pytest
import yaml

from aggregator import (
    parse_proxies,
    rename_proxies,
    split_commas_and_newlines,
    parse_userinfo,
    try_decode_base64,
)


# ─── split_commas_and_newlines ────────────────────────────────────────────────

class TestSplitCommasAndNewlines:
    def test_empty_and_none(self):
        assert split_commas_and_newlines(None) == []
        assert split_commas_and_newlines("") == []
        assert split_commas_and_newlines("   ") == []

    def test_lines_and_commas(self):
        assert split_commas_and_newlines("a,b\nc") == ["a", "b", "c"]
        assert split_commas_and_newlines("a, b") == ["a", "b"]

    def test_list_input(self):
        assert split_commas_and_newlines([" x ", "y"]) == ["x", "y"]

    def test_matches_legacy_behavior(self):
        raw = "10.0.0.0/8, 172.16.0.0/12\n192.168.0.0/16"
        assert split_commas_and_newlines(raw) == ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]


# ─── parse_userinfo ───────────────────────────────────────────────────────────

class TestParseUserinfo:
    def test_standard_header(self):
        header = "upload=1000000; download=5000000; total=10000000000; expire=1700000000"
        info = parse_userinfo(header)
        assert info["used"] == 6000000
        assert info["total"] == 10000000000
        assert info["expire"] == 1700000000

    def test_empty_header(self):
        assert parse_userinfo("") == {}
        assert parse_userinfo(None) == {}

    def test_partial_header(self):
        info = parse_userinfo("upload=100; download=200")
        assert info["used"] == 300
        assert info["total"] == 0


# ─── try_decode_base64 ────────────────────────────────────────────────────────

class TestTryDecodeBase64:
    def test_valid_base64(self):
        encoded = base64.b64encode(b"hello world").decode()
        assert try_decode_base64(encoded) == "hello world"

    def test_invalid_returns_none(self):
        assert try_decode_base64("not!base64!!") is None

    def test_empty_returns_none(self):
        assert try_decode_base64("") is None


# ─── parse_proxies ────────────────────────────────────────────────────────────

_CLASH_YAML_FIXTURE = """
proxies:
  - name: NodeA
    type: ss
    server: 1.2.3.4
    port: 8388
    cipher: aes-256-gcm
    password: pass1
  - name: NodeB
    type: trojan
    server: 5.6.7.8
    port: 443
    password: pass2
"""

_V2RAY_URI_FIXTURE = (
    "trojan://password@1.2.3.4:443#TrojanNode\n"
    "trojan://password2@5.6.7.8:443#TrojanNode2"
)


class TestParseProxies:
    def test_parse_clash_yaml(self):
        proxies = parse_proxies(_CLASH_YAML_FIXTURE)
        assert len(proxies) == 2
        assert proxies[0]["name"] == "NodeA"
        assert proxies[0]["type"] == "ss"
        assert proxies[1]["name"] == "NodeB"

    def test_parse_uri_lines(self):
        proxies = parse_proxies(_V2RAY_URI_FIXTURE)
        assert len(proxies) == 2
        assert proxies[0]["type"] == "trojan"

    def test_parse_base64_encoded_yaml(self):
        encoded = base64.b64encode(_CLASH_YAML_FIXTURE.encode()).decode()
        proxies = parse_proxies(encoded)
        assert len(proxies) == 2

    def test_parse_empty(self):
        assert parse_proxies("") == []
        assert parse_proxies(None) == []

    def test_parse_yaml_with_nbsp(self):
        """从网页粘贴的 YAML 常含 NBSP，应能正常解析。"""
        nbsped = _CLASH_YAML_FIXTURE.replace("  - name", "\u00a0\u00a0- name")
        proxies = parse_proxies(nbsped)
        assert len(proxies) >= 1


# ─── rename_proxies ───────────────────────────────────────────────────────────

class TestRenameProxies:
    def test_prefix_applied(self):
        proxies = [{"name": "Node1", "type": "ss"}, {"name": "Node2", "type": "trojan"}]
        renamed = rename_proxies(proxies, "MyPrefix")
        assert renamed[0]["name"] == "[MyPrefix] Node1"
        assert renamed[1]["name"] == "[MyPrefix] Node2"

    def test_empty_prefix_keeps_name(self):
        proxies = [{"name": "Node1", "type": "ss"}]
        renamed = rename_proxies(proxies, "")
        assert renamed[0]["name"] == "Node1"

    def test_original_not_mutated(self):
        proxies = [{"name": "Node1", "type": "ss"}]
        rename_proxies(proxies, "Prefix")
        assert proxies[0]["name"] == "Node1"
