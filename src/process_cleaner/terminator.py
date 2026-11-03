"""终止执行模块。

职责：按「先优雅、后强制」的顺序终止进程，并妥善处理各类异常。

异常处理策略（保证不崩溃）：
- 进程已退出        → 记为 gone（未发信号或发信号时发现进程已退出）
- 权限不足          → 记录 ERROR 并继续下一个，返回失败清单
- 超时未响应        → 升级为强制终止，仍失败则记录并继续
- 查询或命令失败    → 记录失败，不推断进程已退出

所有操作都返回结构化的统计信息，供 CLI 汇总与日志落盘。
"""

from __future__ import annotations

import csv
import io
import logging
import os
import platform
import subprocess
import time
from dataclasses import dataclass, field
from typing import List, Sequence

from .models import ProcessInfo

logger = logging.getLogger(__name__)

IS_WINDOWS = platform.system().lower() == "windows"


@dataclass
class KillStats:
    """一次终止动作的统计结果。"""

    requested: int = 0
    succeeded: List[int] = field(default_factory=list)   # 成功终止的 PID
    gone: List[int] = field(default_factory=list)        # 执行前已退出的 PID
    failed: List[int] = field(default_factory=list)      # 终止失败的 PID
    freed_mb: float = 0.0

    @property
    def ok(self) -> int:
        return len(self.succeeded) + len(self.gone)


def _process_exists(pid: int) -> bool:
    """判断进程是否仍在运行（Windows 用 tasklist 过滤，POSIX 用 signal 0）。"""
    try:
        if IS_WINDOWS:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, errors="replace",
                timeout=15, check=True,
            ).stdout
            return any(
                len(row) >= 2 and row[1].strip() == str(pid)
                for row in csv.reader(io.StringIO(out))
            )
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        if IS_WINDOWS:
            raise
        # POSIX 下 PermissionError 反而说明进程存在但无权发信号
        return True


def _terminate_posix(pid: int, force: bool) -> None:
    import signal

    sig = signal.SIGKILL if force else signal.SIGTERM
    os.kill(pid, sig)


def _terminate_windows(pid: int, force: bool) -> None:
    # 不带 /F 是"请求关闭"，带 /F 才是强制结束
    cmd = ["taskkill", "/PID", str(pid)]
    if force:
        cmd.append("/F")
    # 只终止经过规则检查的 PID，不能连带终止未授权或受保护的子进程。
    subprocess.run(
        cmd, capture_output=True, text=True, errors="replace",
        timeout=15, check=True,
    )


def _wait_for_exit(pid: int, timeout: float) -> bool:
    """使用单调时钟等待退出；查询失败由调用方记录为失败。"""
    deadline = time.monotonic() + max(timeout, 0.0)
    while _process_exists(pid):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.4, remaining))
    return True


def terminate_one(proc: ProcessInfo, grace_period: float = 5.0, force: bool = False) -> str:
    """终止单个进程，返回状态：success / gone / failed。"""
    pid, label = proc.pid, f"{proc.display_name}(PID {proc.pid})"

    # POSIX 的 0 和负 PID 表示进程组，绝不能作为单进程目标发送信号。
    if pid <= 0:
        logger.error("拒绝无效 PID: %s", label)
        return "failed"

    try:
        if not _process_exists(pid):
            logger.info("进程已退出，无需终止: %s", label)
            return "gone"

        if IS_WINDOWS:
            _terminate_windows(pid, force)
        else:
            _terminate_posix(pid, force)

        if not force:
            if _wait_for_exit(pid, grace_period):
                logger.info("已优雅终止: %s", label)
                return "success"
            logger.warning("优雅终止超时（%.1fs），升级为强制终止: %s", grace_period, label)
            if IS_WINDOWS:
                _terminate_windows(pid, force=True)
            else:
                _terminate_posix(pid, force=True)

        # 发出强制终止命令不代表进程已经退出；等待并核实最终结果。
        if _wait_for_exit(pid, 5.0):
            return "success"
        logger.error("强制终止后进程仍在运行: %s", label)
        return "failed"
    except PermissionError:
        logger.error("权限不足，无法终止: %s（请以管理员/root 权限重试）", label)
        return "failed"
    except ProcessLookupError:
        logger.info("进程在终止前已退出: %s", label)
        return "gone"
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("终止失败: %s -> %s", label, exc)
        return "failed"


def terminate_all(
    processes: Sequence[ProcessInfo],
    grace_period: float = 5.0,
    force: bool = False,
) -> KillStats:
    """批量终止；单个进程失败不影响其余进程。"""
    stats = KillStats(requested=len(processes))
    for proc in processes:
        try:
            status = terminate_one(proc, grace_period=grace_period, force=force)
        except Exception as exc:  # 最后一道防线，绝不让主流程崩掉
            logger.error("终止 %s 时发生未预期异常: %s", proc.display_name, exc)
            status = "failed"

        if status == "success":
            stats.succeeded.append(proc.pid)
            stats.freed_mb += proc.memory_mb
        elif status == "gone":
            stats.gone.append(proc.pid)
        else:
            stats.failed.append(proc.pid)
    return stats
