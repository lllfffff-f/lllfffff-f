"""输出渲染模块：把扫描/过滤/执行结果展示成人类可读的表格。

职责单一：只负责格式化与打印，不做任何判断，方便替换成 JSON/HTML 等其它形式。
"""

from __future__ import annotations

import json
import sys
from typing import Sequence

from .models import ProcessInfo


def _align(text: str, width: int) -> str:
    """按显示宽度对齐（中文按 2 个字符宽度计算，避免终端错位）。"""
    def display_len(s: str) -> int:
        return sum(2 if ord(ch) > 127 else 1 for ch in s)

    pad = max(width - display_len(text), 0)
    return text + " " * pad


def render_processes(processes: Sequence[ProcessInfo], title: str = "待终止进程") -> str:
    """渲染进程表格（名称 / PID / 内存）。"""
    lines = []
    if not processes:
        return f"{title}: 无"

    headers = ("进程名称", "PID", "内存占用(MB)")
    rows = [(p.display_name or p.name, str(p.pid), f"{p.memory_mb:.1f}") for p in processes]
    widths = [
        max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(3)
    ]
    # 名称列做长度截断，避免超长路径破坏排版
    widths[0] = min(widths[0], 42)

    lines.append(f"{title}（共 {len(processes)} 个）:")
    lines.append("  " + "  ".join(_align(headers[i], widths[i]) for i in range(3)))
    lines.append("  " + "  ".join("-" * widths[i] for i in range(3)))
    for row in rows:
        name = row[0] if len(row[0]) <= widths[0] else row[0][: widths[0] - 1] + "…"
        lines.append("  " + _align(name, widths[0]) + "  " + _align(row[1], widths[1]) + "  " + row[2])
    return "\n".join(lines)


def render_summary(result, stats=None, dry_run: bool = True) -> str:
    """渲染汇总信息。"""
    protected_only = [d for d in result.protected if not d.kill]
    lines = [
        "",
        "── 汇总 ──────────────────────────────",
        f"扫描来源      : {result.source}",
        f"扫描进程总数  : {result.scanned}",
        f"命中清理规则  : {len(result.kill)}",
        f"白名单保护    : {len(protected_only)}",
        f"未命中规则    : {len(result.skipped)}",
    ]
    if result.kill:
        lines.append(f"预计释放内存  : {result.total_memory_mb:.1f} MB")
    if stats is not None:
        mode = "预览（未执行）" if dry_run else "已执行"
        lines.append(f"执行模式      : {mode}")
        lines.append(
            f"成功终止      : {len(stats.succeeded)}（含已退出 {len(stats.gone)}），失败 {len(stats.failed)}"
        )
        lines.append(f"实际释放内存  : {stats.freed_mb:.1f} MB")
        if stats.failed:
            lines.append(f"失败 PID      : {', '.join(map(str, stats.failed))}（多为权限不足）")
    lines.append("──────────────────────────────────────")
    return "\n".join(lines)


def render_protected(result, limit: int = 15) -> str:
    """渲染被保护进程的样例及原因，便于用户核对白名单是否合理。"""
    protected_only = [d for d in result.protected if not d.kill]
    if not protected_only:
        return "白名单保护的进程: 无"
    lines = [f"白名单保护的进程（共 {len(protected_only)} 个，展示前 {min(limit, len(protected_only))} 个）:"]
    for decision in protected_only[:limit]:
        name = decision.process.display_name or decision.process.name
        lines.append(f"  - {name} (PID {decision.process.pid}): {decision.reason}")
    return "\n".join(lines)


def render_json(result, stats=None, dry_run: bool = True) -> str:
    """机器可读输出，便于与其它脚本组合。"""
    payload = {
        "dry_run": dry_run,
        "source": result.source,
        "scanned": result.scanned,
        "kill": [
            {"pid": p.pid, "name": p.display_name or p.name, "memory_mb": p.memory_mb}
            for p in result.kill
        ],
        "protected": [
            {"pid": d.process.pid, "name": d.process.display_name or d.process.name,
             "reason": d.reason}
            for d in result.protected if not d.kill
        ],
    }
    if stats is not None:
        payload["result"] = {
            "succeeded": stats.succeeded,
            "gone": stats.gone,
            "failed": stats.failed,
            "freed_mb": round(stats.freed_mb, 1),
        }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def echo(text: str = "", err: bool = False) -> None:
    """统一打印入口，规避不同终端的编码差异。

    err=True 时写到 stderr（JSON 模式下的提示信息，避免污染 stdout 的 JSON）。
    """
    stream = sys.stderr if err else sys.stdout
    try:
        stream.write(text + "\n")
    except UnicodeEncodeError:  # 老旧控制台不支持中文时的降级
        stream.write(text.encode("utf-8", "replace").decode("utf-8", "replace") + "\n")
