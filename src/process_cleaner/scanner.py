"""进程扫描模块。

职责：把当前系统的进程枚举成统一的 ProcessInfo 列表，不做任何过滤或终止决策。

实现思路：
1. 优先使用 psutil（可选依赖），可拿到准确 RSS 与可执行文件路径；
2. 未安装 psutil 时回退到命令行：
   - Windows: tasklist /FO CSV /NH
   - Linux/macOS: ps -eo pid,comm,rss,user,args
3. 任何单进程读取失败（权限不足、进程已退出、字段解析异常）都只跳过该进程，
   不会因为一个进程导致整次扫描崩溃。
"""

from __future__ import annotations

import csv
import io
import logging
import os
import platform
import subprocess
from typing import List, Optional

from .models import ProcessInfo

logger = logging.getLogger(__name__)

BYTES_PER_MB = 1024.0 * 1024.0


def _decode(raw: bytes) -> str:
    """解码命令行输出。

    Windows 的 tasklist 会按系统 ANSI 代码页（简体中文环境为 GBK）输出，
    直接按 UTF-8 解码会把中文进程名变成替换字符；这里依次尝试 UTF-8、
    系统 ANSI 代码页、GBK，最后兜底 latin-1，保证永不抛异常。
    """
    candidates = ["utf-8"]
    if platform.system().lower() == "windows":
        candidates += ["mbcs", "gbk", "cp936"]
    candidates.append("latin-1")
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _run_command(cmd: List[str]) -> str:
    """执行命令并返回解码后的标准输出；命令缺失或超时返回空串。"""
    try:
        completed = subprocess.run(
            cmd, capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("执行 %s 失败: %s", " ".join(cmd), exc)
        return ""
    return _decode(completed.stdout or b"")


def _safe_float(value: object, default: float = 0.0) -> float:
    """把可能带千位分隔符 / 单位的字符串转成 float，失败返回默认值。"""
    if value is None:
        return default
    text = str(value).strip().replace(",", "").replace(" ", "")
    text = text.upper().replace("KB", "").replace("K", "").strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _pid_of_self_tree() -> set:
    """自身进程 + 所有祖先进程 PID，用于防止自杀/杀掉启动它的终端。"""
    pids: set = {os.getpid()}
    try:
        import psutil  # type: ignore

        proc = psutil.Process(os.getpid())
        for parent in proc.parents():
            pids.add(parent.pid)
    except Exception:  # psutil 缺失或进程已退出，退化为仅保护自身
        ppid = os.getppid()
        if ppid:
            pids.add(ppid)
    return pids


def self_pid_tree() -> set:
    """对外暴露自身进程树（惰性计算，异常时至少保护自身）。"""
    try:
        return _pid_of_self_tree()
    except Exception:
        return {os.getpid()}


# --------------------------------------------------------------------------
# psutil 模式
# --------------------------------------------------------------------------
def _scan_with_psutil() -> Optional[List[ProcessInfo]]:
    try:
        import psutil  # type: ignore
    except ImportError:
        return None

    results: List[ProcessInfo] = []
    for proc in psutil.process_iter():
        try:
            with proc.oneshot():
                info = proc.as_dict(
                    attrs=["pid", "name", "exe", "username", "cmdline", "memory_info"]
                )
            mem = info.get("memory_info")
            mem_mb = round(mem.rss / BYTES_PER_MB, 1) if mem else 0.0
            cmdline = info.get("cmdline") or []
            results.append(
                ProcessInfo(
                    pid=int(info["pid"]),
                    name=str(info.get("name") or ""),
                    memory_mb=mem_mb,
                    exe=info.get("exe"),
                    username=info.get("username"),
                    cmdline=" ".join(cmdline) if cmdline else None,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue  # 进程已退出 / 权限不足：跳过，不中断扫描
        except Exception as exc:  # 兜底，任何异常都不应打断整体扫描
            logger.debug("读取进程信息失败，已跳过: %s", exc)
            continue
    return results


# --------------------------------------------------------------------------
# 命令行回退模式
# --------------------------------------------------------------------------
def _scan_windows_tasklist() -> List[ProcessInfo]:
    """Windows: tasklist /FO CSV /NH → name,pid,session,session#,mem(KB)。"""
    output = _run_command(["tasklist", "/FO", "CSV", "/NH"])

    results: List[ProcessInfo] = []
    reader = csv.reader(io.StringIO(output))
    for row in reader:
        if len(row) < 5:
            continue
        name, pid_text, _session, _session_no, mem_text = row[:5]
        pid_text = pid_text.strip().strip('"')
        if not pid_text.isdigit():
            continue
        # tasklist 的内存单位是 KB 且可能带千位分隔符，如 "1,234 K"
        mem_mb = round(_safe_float(mem_text) / 1024.0, 1)
        results.append(
            ProcessInfo(
                pid=int(pid_text),
                name=name.strip().strip('"'),
                memory_mb=mem_mb,
            )
        )
    return results


def _scan_posix_ps() -> List[ProcessInfo]:
    """Linux/macOS: ps -eo pid,comm,rss,user,args（rss 单位为 KB）。"""
    output = _run_command(["ps", "-eo", "pid=,comm=,rss=,user=,args="])

    results: List[ProcessInfo] = []
    for line in output.splitlines():
        parts = line.strip().split(None, 4)
        if len(parts) < 3:
            continue
        pid_text, name, rss_text = parts[0], parts[1], parts[2]
        if not pid_text.isdigit():
            continue
        results.append(
            ProcessInfo(
                pid=int(pid_text),
                name=name,
                memory_mb=round(_safe_float(rss_text) / 1024.0, 1),
                username=parts[3] if len(parts) > 3 else None,
                cmdline=parts[4] if len(parts) > 4 else None,
            )
        )
    return results


def scan_processes(prefer: str = "auto") -> tuple:
    """扫描全系统进程。

    返回 (processes, source)，source 为 "psutil" / "tasklist" / "ps"。
    prefer="cli" 可强制走命令行模式（便于测试与对比）。
    """
    if prefer != "cli":
        from_psutil = _scan_with_psutil()
        if from_psutil:
            return from_psutil, "psutil"

    system = platform.system().lower()
    if system == "windows":
        return _scan_windows_tasklist(), "tasklist"
    return _scan_posix_ps(), "ps"
