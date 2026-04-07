"""
回归测试：重构后行为与文案保持一致（split_csv_or_lines、format_probe_success_message）。
从仓库根目录执行: python -m unittest tests.test_regression -v
"""
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_APP = _ROOT / "app"
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

from aggregator import split_csv_or_lines  # noqa: E402
from proxy_latency import format_probe_success_message  # noqa: E402


class TestSplitCsvOrLines(unittest.TestCase):
    def test_empty_and_none(self):
        self.assertEqual(split_csv_or_lines(None), [])
        self.assertEqual(split_csv_or_lines(""), [])
        self.assertEqual(split_csv_or_lines("   "), [])

    def test_lines_and_commas(self):
        self.assertEqual(split_csv_or_lines("a,b\nc"), ["a", "b", "c"])
        self.assertEqual(split_csv_or_lines("a, b"), ["a", "b"])

    def test_list_input(self):
        self.assertEqual(split_csv_or_lines([" x ", "y"]), ["x", "y"])

    def test_matches_legacy_main_behavior(self):
        """与原先 main._split_csv_or_lines 对 str 输入的行为一致。"""
        raw = "10.0.0.0/8, 172.16.0.0/12\n192.168.0.0/16"
        self.assertEqual(
            split_csv_or_lines(raw),
            ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
        )


class TestFormatProbeSuccessMessage(unittest.TestCase):
    def test_import_style_with_suffix(self):
        s = format_probe_success_message("httpx", 12.3, suffix="（提示）")
        self.assertIn("经代理访问测试 URL", s)
        self.assertIn("12", s)
        self.assertTrue(s.endswith("（提示）"))

    def test_stateless_same_as_import_no_suffix(self):
        for kind in ("httpx", "mihomo", "tcp-fallback"):
            a = format_probe_success_message(kind, 10.0, suffix="")
            b = format_probe_success_message(kind, 10.0)
            self.assertEqual(a, b)

    def test_single_subscription_prefix(self):
        m = format_probe_success_message("httpx", 5.0, single_subscription=True)
        self.assertTrue(m.startswith("可用，1 个节点；"))
        self.assertNotIn("suffix", m)

    def test_unknown_kind(self):
        self.assertEqual(format_probe_success_message("none", 0.0), "可用")
        self.assertEqual(format_probe_success_message("none", 0.0, suffix=" X"), "可用 X")
        self.assertEqual(format_probe_success_message("none", 0.0, single_subscription=True), "可用，1 个节点")


if __name__ == "__main__":
    unittest.main()
