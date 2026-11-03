"""CLI 测试：真实扫描只做预览，执行路径全部模拟，绝不终止真实进程。"""

import json
import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_PY = os.path.join(ROOT, "run.py")
sys.path.insert(0, os.path.join(ROOT, "src"))

from process_cleaner import cli
from process_cleaner.models import ProcessInfo
from process_cleaner.rules import load_config
from process_cleaner.terminator import KillStats


def run_cli(*args):
    return subprocess.run(
        [sys.executable, RUN_PY, "--dry-run", "--log-dir", "", *args],
        capture_output=True, cwd=ROOT, timeout=120,
    )


class TestCliDryRun(unittest.TestCase):
    def test_text_mode_runs_and_reports_summary(self):
        proc = run_cli()
        self.assertIn(proc.returncode, (0, 2), proc.stderr.decode("utf-8", "replace"))
        self.assertIn("扫描进程总数", proc.stdout.decode("utf-8", "replace"))

    def test_json_mode_stdout_is_pure_json(self):
        """--format json 的 stdout 必须能被直接解析，日志与提示只能走 stderr。"""
        proc = run_cli("--format", "json", "--use-cli-scanner")
        self.assertIn(proc.returncode, (0, 2), proc.stderr.decode("utf-8", "replace"))
        payload = json.loads(proc.stdout.decode("utf-8"))
        self.assertTrue(payload["dry_run"])
        self.assertGreater(payload["scanned"], 0)
        self.assertIn("INFO", proc.stderr.decode("utf-8", "replace"))

    def test_json_mode_with_match_rule_stays_dry_run(self):
        # 匹配一个必然存在却受白名单保护的进程，验证预览模式下 kill 列表为空且 JSON 合法
        proc = run_cli("--format", "json", "--name", "explorer.exe")
        payload = json.loads(proc.stdout.decode("utf-8"))
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["kill"], [])


class TestCliExecution(unittest.TestCase):
    """Execution paths use a fake scanner and never call the real terminator."""

    def setUp(self):
        config = load_config()
        config["match"]["names"] = ["test-target.exe"]
        self.add_patch("load_config", return_value=config)
        self.add_patch("setup_logging")
        self.add_patch("force_utf8_io")
        self.scan = self.add_patch("scan_processes", return_value=(
            [ProcessInfo(999999, "test-target.exe", 25)], "mock"))
        self.terminate = self.add_patch("terminate_all", return_value=KillStats(
            requested=1, succeeded=[999999], freed_mb=25))

    def add_patch(self, name, **kwargs):
        patcher = patch.object(cli, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def invoke(self, *args, answer="yes"):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), patch("builtins.input", return_value=answer):
            status = cli.main(["--format", "json", "--log-dir", "", *args])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_execute_yes_outputs_one_json_document(self):
        status, out, err = self.invoke("--execute", "--yes")
        payload = json.loads(out)
        self.assertEqual(status, cli.EXIT_OK)
        self.assertFalse(payload["dry_run"])
        self.assertEqual(payload["result"]["succeeded"], [999999])
        self.assertIn("test-target.exe", err)
        self.terminate.assert_called_once()

    def test_interactive_confirmation_stays_on_stderr(self):
        status, out, err = self.invoke("--execute")
        self.assertEqual(status, cli.EXIT_OK)
        self.assertIn("result", json.loads(out))
        self.assertIn("yes", err)

    def test_cancel_returns_preview_and_does_not_terminate(self):
        status, out, err = self.invoke("--execute", answer="no")
        self.assertEqual(status, cli.EXIT_ABORT)
        self.assertTrue(json.loads(out)["dry_run"])
        self.terminate.assert_not_called()

    def test_execution_failure_returns_one_json_result(self):
        self.terminate.return_value = KillStats(requested=1, failed=[999999])
        status, out, err = self.invoke("--execute", "--yes")
        self.assertEqual(status, cli.EXIT_ERROR)
        self.assertEqual(json.loads(out)["result"]["failed"], [999999])

    def test_scanner_error_does_not_write_text_to_stdout(self):
        for result in ([], None):
            with self.subTest(result=result):
                if result is None:
                    self.scan.side_effect = PermissionError("test")
                else:
                    self.scan.return_value = (result, "mock")
                status, out, err = self.invoke()
                self.assertEqual(status, cli.EXIT_ERROR)
                self.assertEqual(out, "")
                self.assertTrue(err)
                self.terminate.assert_not_called()

    def test_nonpositive_top_is_rejected_before_scanning(self):
        for value in ("0", "-1"):
            with self.subTest(value=value), self.assertRaises(SystemExit) as error:
                self.invoke("--top", value)
            self.assertEqual(error.exception.code, 2)
        self.scan.assert_not_called()
        self.terminate.assert_not_called()

    def test_positive_top_limits_candidates(self):
        self.scan.return_value = ([
            ProcessInfo(999998, "test-target.exe", 10),
            ProcessInfo(999999, "test-target.exe", 25),
        ], "mock")
        status, out, err = self.invoke("--top", "1")
        self.assertEqual(status, cli.EXIT_OK)
        self.assertEqual([p["pid"] for p in json.loads(out)["kill"]], [999999])
        self.terminate.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
