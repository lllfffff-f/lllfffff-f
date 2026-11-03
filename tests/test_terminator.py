"""Termination regression tests: all OS commands and signals are mocked."""

import os
import subprocess
import sys
import unittest
from unittest.mock import call, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from process_cleaner import terminator
from process_cleaner.models import ProcessInfo


class TestTermination(unittest.TestCase):
    def setUp(self):
        self.run = self.enter_patch("subprocess.run")
        self.kill = self.enter_patch("os.kill")
        self.enter_patch("time.sleep")
        self.proc = ProcessInfo(1234, "test.exe", memory_mb=25)

    def enter_patch(self, target, **kwargs):
        patcher = patch("process_cleaner.terminator." + target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_posix_missing_process_is_absent(self):
        self.enter_patch("IS_WINDOWS", new=False)
        self.kill.side_effect = ProcessLookupError
        self.assertFalse(terminator._process_exists(1234))

    def test_posix_permission_denied_means_present(self):
        self.enter_patch("IS_WINDOWS", new=False)
        self.kill.side_effect = PermissionError
        self.assertTrue(terminator._process_exists(1234))

    def test_windows_presence_matches_pid_column_only(self):
        self.enter_patch("IS_WINDOWS", new=True)
        for output, expected in [
            ('"app.exe","1234","Console","1","50 K"', True),
            ('"1234.exe","5678","Console","1","1234 K"', False),
            ('"app.exe","12345","Console","1","50 K"', False),
            ("INFO: No tasks are running which match the specified criteria.", False),
        ]:
            with self.subTest(output=output):
                self.run.return_value.stdout = output
                self.assertEqual(terminator._process_exists(1234), expected)
        self.assertTrue(self.run.call_args.kwargs["check"])

    def test_failed_presence_check_is_not_reported_as_gone(self):
        self.enter_patch("IS_WINDOWS", new=True)
        for error in (OSError("missing tasklist"), PermissionError("cannot query"),
                      subprocess.TimeoutExpired("tasklist", 15),
                      subprocess.CalledProcessError(1, "tasklist")):
            with self.subTest(error=error):
                self.run.side_effect = error
                self.assertEqual(terminator.terminate_one(self.proc), "failed")
        self.assertTrue(all(c.args[0][0] == "tasklist" for c in self.run.call_args_list))

    def test_windows_force_only_targets_selected_pid(self):
        terminator._terminate_windows(1234, force=True)
        self.assertEqual(self.run.call_args.args[0], ["taskkill", "/PID", "1234", "/F"])
        self.assertTrue(self.run.call_args.kwargs["check"])

    def test_failed_taskkill_is_not_success(self):
        self.enter_patch("IS_WINDOWS", new=True)
        self.enter_patch("_process_exists", return_value=True)
        self.run.side_effect = subprocess.CalledProcessError(1, "taskkill")
        self.assertEqual(terminator.terminate_one(self.proc, force=True), "failed")

    def test_force_requires_observed_exit(self):
        self.enter_patch("IS_WINDOWS", new=True)
        self.enter_patch("_process_exists", return_value=True)
        wait = self.enter_patch("_wait_for_exit")
        for exited, expected in [(True, "success"), (False, "failed")]:
            with self.subTest(exited=exited):
                wait.return_value = exited
                self.assertEqual(terminator.terminate_one(self.proc, force=True), expected)
                wait.assert_called_with(1234, 5.0)

    def test_graceful_timeout_escalates_then_verifies(self):
        self.enter_patch("IS_WINDOWS", new=True)
        self.enter_patch("_process_exists", return_value=True)
        self.enter_patch("_wait_for_exit", side_effect=[False, True])
        terminate = self.enter_patch("_terminate_windows")
        self.assertEqual(terminator.terminate_one(self.proc), "success")
        self.assertEqual(terminate.call_args_list, [call(1234, False), call(1234, force=True)])

    def test_graceful_exit_does_not_force(self):
        self.enter_patch("IS_WINDOWS", new=True)
        self.enter_patch("_process_exists", return_value=True)
        self.enter_patch("_wait_for_exit", return_value=True)
        terminate = self.enter_patch("_terminate_windows")
        self.assertEqual(terminator.terminate_one(self.proc), "success")
        terminate.assert_called_once_with(1234, False)

    def test_gone_process_receives_no_signal(self):
        self.enter_patch("_process_exists", return_value=False)
        self.assertEqual(terminator.terminate_one(self.proc), "gone")
        self.kill.assert_not_called()
        self.run.assert_not_called()

    def test_invalid_pid_never_reaches_os(self):
        for pid in (0, -1, -123):
            with self.subTest(pid=pid):
                self.assertEqual(terminator.terminate_one(ProcessInfo(pid, "bad")), "failed")
        self.kill.assert_not_called()
        self.run.assert_not_called()

    def test_wait_observes_delayed_exit(self):
        self.enter_patch("_process_exists", side_effect=[True, False])
        self.enter_patch("time.monotonic", side_effect=[0, 0])
        self.assertTrue(terminator._wait_for_exit(1234, 5))

    def test_wait_timeout_uses_monotonic_clock(self):
        self.enter_patch("_process_exists", return_value=True)
        self.enter_patch("time.monotonic", side_effect=[0, 0, 5])
        self.assertFalse(terminator._wait_for_exit(1234, 5))

    def test_batch_continues_and_counts_only_confirmed_success(self):
        self.enter_patch("terminate_one", side_effect=["failed", "success", "gone"])
        stats = terminator.terminate_all([ProcessInfo(pid, "test", 10) for pid in (1, 2, 3)])
        self.assertEqual(stats.failed, [1])
        self.assertEqual(stats.succeeded, [2])
        self.assertEqual(stats.gone, [3])
        self.assertEqual(stats.freed_mb, 10)


if __name__ == "__main__":
    unittest.main()
