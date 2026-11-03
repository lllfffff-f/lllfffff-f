"""规则引擎单元测试：只覆盖过滤逻辑，绝不真的终止任何进程。

使用标准库 unittest，无需安装任何第三方包即可运行：
    python -m unittest discover -s tests -v
（若环境已安装 pytest，也可直接 `pytest tests`）
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from process_cleaner.models import ProcessInfo  # noqa: E402
from process_cleaner.rules import (  # noqa: E402
    BUILTIN_EDITORS_TERMINALS,
    BUILTIN_SYSTEM_PROTECTED,
    RuleEngine,
    load_config,
)


def make_proc(pid=1001, name="foo.exe", mem=10.0, exe=None, user="user"):
    return ProcessInfo(pid=pid, name=name, memory_mb=mem, exe=exe, username=user)


def engine_with(**match):
    config = load_config(None)          # 内置默认配置
    config["match"].update(match)
    return RuleEngine(config)


class TestWhitelist(unittest.TestCase):
    """白名单：一票否决。"""

    def test_system_critical_process_is_never_killed(self):
        engine = engine_with(name_patterns=[".*"])     # 规则匹配一切
        result = engine.evaluate([make_proc(name="svchost.exe")])
        self.assertEqual(result.kill, [])
        self.assertTrue(result.protected[0].reason.startswith("系统关键进程"))

    def test_editor_and_terminal_are_protected(self):
        engine = engine_with(names=["code.exe", "cmd.exe", "bash.exe"])
        result = engine.evaluate(
            [make_proc(pid=1, name="code.exe"), make_proc(pid=2, name="cmd.exe")]
        )
        self.assertEqual(result.kill, [])
        self.assertEqual(len(result.protected), 2)

    def test_self_process_is_protected(self):
        engine = engine_with(name_patterns=[".*"])
        result = engine.evaluate([make_proc(pid=os.getpid(), name="python.exe")])
        self.assertEqual(result.kill, [])

    def test_protection_wins_over_match(self):
        """白名单优先级必须高于匹配规则，避免配置冲突时误杀。"""
        engine = engine_with(names=["explorer.exe"])
        result = engine.evaluate([make_proc(name="explorer.exe")])
        self.assertEqual(result.kill, [])

    def test_custom_path_prefix_protection(self):
        engine = engine_with(name_patterns=[".*"])
        engine.protect_paths = ["d:/tools/"]
        result = engine.evaluate([make_proc(exe="D:\\Tools\\a.exe")])
        self.assertEqual(result.kill, [])

    def test_system_user_account_protected(self):
        engine = engine_with(name_patterns=[".*"])
        result = engine.evaluate([make_proc(name="helper.exe", user="SYSTEM")])
        self.assertEqual(result.kill, [])


class TestMatchRules(unittest.TestCase):
    """匹配规则：显式授权才清理。"""

    def test_default_config_kills_nothing(self):
        """默认规则为空 → 任何进程都不应被选中，这是最重要的安全底线。"""
        engine = RuleEngine(load_config(None))
        result = engine.evaluate([make_proc(name="random_app.exe", mem=9999)])
        self.assertEqual(result.kill, [])

    def test_name_match_and_memory_threshold(self):
        engine = engine_with(names=["foo.exe"], min_memory_mb=50)
        result = engine.evaluate([
            make_proc(pid=1, name="foo.exe", mem=100),
            make_proc(pid=2, name="foo.exe", mem=10),   # 低于阈值
            make_proc(pid=3, name="bar.exe", mem=100),  # 未命中
        ])
        self.assertEqual([p.pid for p in result.kill], [1])

    def test_pattern_match_is_case_insensitive(self):
        engine = engine_with(name_patterns=["^helper"])
        result = engine.evaluate(
            [make_proc(name="HelperApp.exe"), make_proc(pid=2, name="other.exe")]
        )
        self.assertEqual([p.pid for p in result.kill], [1001])

    def test_excluded_names_override_match(self):
        engine = engine_with(names=["app.exe"], excluded_names=["app.exe"])
        result = engine.evaluate([make_proc(name="app.exe")])
        self.assertEqual(result.kill, [])

    def test_max_count_truncates_and_keeps_highest_memory(self):
        engine = engine_with(name_patterns=[".*"], max_count=2)
        result = engine.evaluate([
            make_proc(pid=1, mem=10), make_proc(pid=2, mem=300), make_proc(pid=3, mem=200),
        ])
        self.assertEqual([p.pid for p in result.kill], [2, 3])


class TestConfigRobustness(unittest.TestCase):
    """配置异常时不得崩溃，必须回退到安全默认值。"""

    def test_invalid_config_file_falls_back_to_defaults(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.json")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            config = load_config(bad)
        self.assertEqual(config["match"]["names"], [])

    def test_missing_config_file_falls_back(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(os.path.join(tmp, "nope.json"))
        self.assertTrue(config["protect"]["system_critical"])

    def test_invalid_regex_is_skipped_not_crashing(self):
        engine = engine_with(name_patterns=["([", "foo"])
        result = engine.evaluate([make_proc(name="foo.exe")])
        self.assertEqual(len(result.kill), 1)


class TestBuiltinLists(unittest.TestCase):
    def test_builtin_lists_cover_key_processes(self):
        for name in ("explorer.exe", "lsass.exe", "csrss.exe", "winlogon.exe", "systemd"):
            self.assertIn(name, BUILTIN_SYSTEM_PROTECTED)
        for name in ("code.exe", "cmd.exe", "powershell.exe", "windowsterminal.exe"):
            self.assertIn(name, BUILTIN_EDITORS_TERMINALS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
