"""日志模块：控制台输出 + 操作日志落盘。

职责：
1. 统一日志格式，控制台只输出必要信息（INFO 及以上），避免刷屏；
2. 每次运行把完整过程写入 logs/cleaner-YYYYMMDD.log，便于事后审计追责；
3. 日志文件按天滚动，写入失败（如无写权限）时降级为仅控制台输出。
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from typing import Optional, TextIO

LOG_DIRNAME = "logs"
CONSOLE_FORMAT = "%(levelname)s: %(message)s"
FILE_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    log_dir: Optional[str] = None,
    verbose: bool = False,
    stream: Optional[TextIO] = None,
) -> Optional[str]:
    """初始化日志，返回日志文件路径（失败则返回 None）。

    stream: 控制台日志输出流，默认 stdout；JSON 模式下传 stderr，保证 stdout 只有纯 JSON。
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in list(root.handlers):  # 避免重复初始化时叠加 handler
        root.removeHandler(handler)

    console = logging.StreamHandler(stream if stream is not None else sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    root.addHandler(console)

    if not log_dir:
        return None

    try:
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(
            log_dir, f"cleaner-{datetime.now().strftime('%Y%m%d')}.log"
        )
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(FILE_FORMAT, DATE_FORMAT))
        root.addHandler(file_handler)
        return path
    except OSError as exc:  # 无写权限 / 路径非法：降级为仅控制台
        logging.getLogger(__name__).warning("无法创建日志文件（%s），仅输出到控制台", exc)
        return None


def log_scan_result(logger: logging.Logger, result) -> None:
    """把扫描与过滤结果写入日志（含被保护进程的原因，便于审计）。"""
    logger.info(
        "扫描来源=%s，共扫描 %d 个进程；命中 %d 个，保护 %d 个，未命中 %d 个",
        result.source, result.scanned, len(result.kill),
        len([d for d in result.protected if not d.kill]),
        len(result.skipped),
    )
    for decision in result.protected:
        if decision.kill:
            continue
        logger.debug("保护: %s (PID %d) -> %s",
                     decision.process.display_name, decision.process.pid, decision.reason)


def log_action(logger: logging.Logger, result, stats, dry_run: bool) -> None:
    """记录最终动作结果。"""
    prefix = "[DRY-RUN] " if dry_run else ""
    logger.info(
        "%s请求终止 %d 个进程；成功 %d，已退出 %d，失败 %d；释放内存约 %.1f MB",
        prefix, stats.requested, len(stats.succeeded),
        len(stats.gone), len(stats.failed), stats.freed_mb,
    )
    if stats.failed:
        logger.warning("以下 PID 终止失败（多为权限不足）: %s", ", ".join(map(str, stats.failed)))
    for proc in result.kill:
        logger.debug("%s终止目标: %s (PID %d, %.1f MB)", prefix,
                     proc.display_name, proc.pid, proc.memory_mb)
