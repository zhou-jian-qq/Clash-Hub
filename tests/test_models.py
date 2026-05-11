"""测试 models 模块：parse_user_agent 及 SubAccessLog.to_dict 结构化字段。"""
import sys
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent / "app"
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

import pytest
from models import parse_user_agent, SubAccessLog


class TestParseUserAgent:
    def test_empty_none_returns_empty(self):
        assert parse_user_agent(None) == ("", "", "")
        assert parse_user_agent("") == ("", "", "")

    def test_clash_with_version(self):
        c, cv, cd = parse_user_agent("clash/1.18.0")
        assert c == "Clash"
        assert cv == "1.18.0"
        assert cd == "Clash 1.18.0"

    def test_clash_meta_android(self):
        c, cv, cd = parse_user_agent("ClashMetaForAndroid/2.5.6")
        assert c == "Clash Meta Android"
        assert cv == "2.5.6"
        assert cd == "Clash Meta Android 2.5.6"

    def test_clash_verge(self):
        c, cv, cd = parse_user_agent("Clash Verge/v1.3.8")
        assert c == "Clash Verge"
        assert cv == "1.3.8"

    def test_surge_ios(self):
        c, cv, cd = parse_user_agent("Surge iOS/5.2.1")
        assert c == "Surge"
        assert cv == "5.2.1"

    def test_shadowrocket(self):
        c, cv, cd = parse_user_agent("Shadowrocket/2.2.30")
        assert c == "Shadowrocket"
        assert cv == "2.2.30"

    def test_v2rayn(self):
        c, cv, cd = parse_user_agent("v2rayN/6.23")
        assert c == "v2rayN"
        assert cv == "6.23"

    def test_v2rayng(self):
        c, cv, cd = parse_user_agent("v2rayNG/1.8.5")
        assert c == "v2rayNG"
        assert cv == "1.8.5"

    def test_sing_box(self):
        c, cv, cd = parse_user_agent("sing-box/1.8.0")
        assert c == "sing-box"
        assert cv == "1.8.0"

    def test_hiddify(self):
        c, cv, cd = parse_user_agent("HiddifyNext/2.3")
        assert c == "Hiddify"
        assert cv == "2.3"

    def test_mihomo(self):
        c, cv, cd = parse_user_agent("mihomo/v1.27.5")
        assert c == "Mihomo"
        assert cv == "1.27.5"

    def test_nekobox(self):
        c, cv, cd = parse_user_agent("NekoBoxForAndroid/3.1")
        assert c == "NekoBox Android"
        assert cv == "3.1"

    def test_clashx(self):
        c, cv, cd = parse_user_agent("ClashX/1.30.2")
        assert c == "ClashX"
        assert cv == "1.30.2"

    def test_urlencoded_ua(self):
        """User-Agent 中的 %20 应等价于空格。"""
        c, cv, cd = parse_user_agent("Clash%20Verge/v1.3.8")
        assert c == "Clash Verge"
        assert cv == "1.3.8"

    def test_unknown_ua_returns_empty(self):
        c, cv, cd = parse_user_agent("Mozilla/5.0")
        assert c == ""
        assert cv == ""
        assert cd == ""

    def test_clash_premium(self):
        c, cv, cd = parse_user_agent("Clash-premium/1.17.0")
        assert c == "Clash"
        assert cv == "1.17.0"

    def test_clash_for_windows(self):
        c, cv, cd = parse_user_agent("ClashforWindows/0.20.29")
        assert c == "Clash for Windows"
        assert cv == "0.20.29"

    def test_quantumult_x(self):
        c, cv, cd = parse_user_agent("Quantumult X/1.4.0")
        assert c == "Quantumult X"
        assert cv == "1.4.0"

    def test_loon(self):
        c, cv, cd = parse_user_agent("Loon/3.0.2")
        assert c == "Loon"
        assert cv == "3.0.2"

    def test_mihomo_party(self):
        c, cv, cd = parse_user_agent("mihomo party/1.3.0")
        assert c == "Mihomo Party"
        assert cv == "1.3.0"

    def test_name_only_fallback(self):
        """无版本号时返回名称但不含版本。"""
        c, cv, cd = parse_user_agent("ClashforWindows")
        assert c == "Clash for Windows"
        assert cv == ""
        assert cd == "Clash for Windows"


class TestSubAccessLogToDict:
    def test_includes_structured_client_fields(self):
        log = SubAccessLog(id=1, ip="127.0.0.1", real_ip=None,
                           user_agent="Clash/1.18.0")
        d = log.to_dict()
        assert d["client"] == "Clash"
        assert d["client_version"] == "1.18.0"
        assert d["client_display"] == "Clash 1.18.0"

    def test_empty_ua_yields_empty_fields(self):
        log = SubAccessLog(id=2, ip="127.0.0.1", real_ip=None,
                           user_agent="")
        d = log.to_dict()
        assert d["client"] == ""
        assert d["client_version"] == ""
        assert d["client_display"] == ""

    def test_none_ua_yields_empty_fields(self):
        log = SubAccessLog(id=3, ip="127.0.0.1", real_ip=None,
                           user_agent=None)
        d = log.to_dict()
        assert d["client"] == ""
        assert d["client_version"] == ""
        assert d["client_display"] == ""
