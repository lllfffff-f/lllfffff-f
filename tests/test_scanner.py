"""扫描器测试：只读取进程，不做任何终止动作。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from process_cleaner import scanner  # noqa: E402


class TestHelpers(unittest.TestCase):
    def test_safe_float_handles_thousands_separator_and_unit(self):
        self.assertEqual(scanner._safe_float("1,234 K"), 1234.0)
        self.assertEqual(scanner._safe_float("12,345 KB"), 12345.0)
        self.assertEqual(scanner._safe_float("abc"), 0.0)
        self.assertEqual(scanner._safe_float(None), 0.0)

    def test_decode_windows_ansi_bytes(self):
        """简体中文 Windows 下 tasklist 输出 GBK 字节，必须能正确解码。"""
        raw = '"\xdf\xd9\xc1\xa8\xdf\xd9\xc1\xa8.exe","25504"'.encode("latin-1")
        text = scanner._decode(raw)
        self.assertNotIn("�", text)
        self.assertTrue(text.endswith("25504\""))

    def test_self_pid_tree_includes_current_process(self):
        self.assertIn(os.getpid(), scanner.self_pid_tree())


class TestScan(unittest.TestCase):
    def test_scan_returns_non_empty_and_valid_pids(self):
        processes, source = scanner.scan_processes()
        self.assertIn(source, {"psutil", "tasklist", "ps"})
        self.assertGreater(len(processes), 0)
        self.assertTrue(all(p.pid >= 0 for p in processes))
        self.assertTrue(all(isinstance(p.memory_mb, float) for p in processes))

    def test_scan_cli_mode_works_without_psutil(self):
        processes, source = scanner.scan_processes(prefer="cli")
        self.assertTrue(processes, "命令行回退模式也应能取到进程")
        self.assertIn(source, {"tasklist", "ps"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
