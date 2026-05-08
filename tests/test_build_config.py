"""
测试 aggregator.build_config 与 preset_templates 的三套预设都能产出合法 Clash YAML。
"""
import sys
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent / "app"
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

import pytest
import yaml

from aggregator import build_config
from preset_templates import PRESETS, get_preset_names


_SAMPLE_PROXY = {
    "name": "TestNode",
    "type": "ss",
    "server": "1.2.3.4",
    "port": 8388,
    "cipher": "aes-256-gcm",
    "password": "testpass",
}


class TestGetPresetNames:
    def test_returns_list(self):
        names = get_preset_names()
        assert isinstance(names, list)
        assert len(names) >= 1

    def test_expected_presets_present(self):
        names = get_preset_names()
        for expected in ("精简版", "标准版", "完整版"):
            assert expected in names, f"预设 {expected} 缺失"


class TestBuildConfig:
    @pytest.mark.parametrize("preset_name", get_preset_names())
    def test_preset_produces_valid_yaml(self, preset_name):
        yaml_text = build_config(
            proxies=[_SAMPLE_PROXY],
            template_name=preset_name,
            custom_template=None,
            include_types=None,
            exclude_types=None,
            exclude_keywords=None,
        )
        assert isinstance(yaml_text, str)
        assert len(yaml_text) > 0
        parsed = yaml.safe_load(yaml_text)
        assert isinstance(parsed, dict)

    @pytest.mark.parametrize("preset_name", get_preset_names())
    def test_preset_has_required_keys(self, preset_name):
        yaml_text = build_config(
            proxies=[_SAMPLE_PROXY],
            template_name=preset_name,
            custom_template=None,
            include_types=None,
            exclude_types=None,
            exclude_keywords=None,
        )
        parsed = yaml.safe_load(yaml_text)
        assert "proxies" in parsed, f"{preset_name}: 缺少 proxies"
        assert "proxy-groups" in parsed, f"{preset_name}: 缺少 proxy-groups"
        assert "rules" in parsed, f"{preset_name}: 缺少 rules"

    def test_empty_proxies_builds_placeholder(self):
        yaml_text = build_config(
            proxies=[],
            template_name="标准版",
            custom_template=None,
            include_types=None,
            exclude_types=None,
            exclude_keywords=None,
        )
        assert isinstance(yaml_text, str)

    def test_include_types_filter(self):
        proxies = [
            {"name": "SS-Node", "type": "ss", "server": "1.1.1.1", "port": 443, "cipher": "aes-256-gcm", "password": "p"},
            {"name": "Trojan-Node", "type": "trojan", "server": "2.2.2.2", "port": 443, "password": "p"},
        ]
        yaml_text = build_config(
            proxies=proxies,
            template_name="标准版",
            custom_template=None,
            include_types=["ss"],
            exclude_types=None,
            exclude_keywords=None,
        )
        parsed = yaml.safe_load(yaml_text)
        proxy_types = [p["type"] for p in parsed.get("proxies", [])]
        assert "trojan" not in proxy_types

    def test_exclude_keyword_filter(self):
        proxies = [
            {"name": "官网节点", "type": "ss", "server": "1.1.1.1", "port": 443, "cipher": "aes-256-gcm", "password": "p"},
            {"name": "普通节点", "type": "ss", "server": "2.2.2.2", "port": 443, "cipher": "aes-256-gcm", "password": "p"},
        ]
        yaml_text = build_config(
            proxies=proxies,
            template_name="标准版",
            custom_template=None,
            include_types=None,
            exclude_types=None,
            exclude_keywords=["官网"],
        )
        parsed = yaml.safe_load(yaml_text)
        proxy_names = [p["name"] for p in parsed.get("proxies", [])]
        assert not any("官网" in n for n in proxy_names)
