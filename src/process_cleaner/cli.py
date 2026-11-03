"""命令行入口：参数解析、流程编排、用户确认。

流程：
  解析参数 → 加载配置 → 扫描进程 → 规则过滤 → 渲染待终止清单
  →（执行模式）用户确认 → 逐个优雅终止 → 汇总与日志

安全约定：
  * 默认走 --dry-run，只有显式传入 --execute 才会真正终止进程；
  * 执行前必须打印完整清单，并由用户输入 "yes" 二次确认（--yes 可跳过交互）；
  * 任何未预期的异常都在此捕获，转成友好提示并返回非 0 退出码，避免堆栈吓人。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import List, Optional, Sequence

from . import __version__
from .logging_setup import log_action, log_scan_result, setup_logging
from .models import ProcessInfo
from .report import echo, render_json, render_processes, render_protected, render_summary
from .rules import RuleEngine, load_config
from .scanner import scan_processes
from .terminator import terminate_all

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOTHING = 2
EXIT_ABORT = 3


# --------------------------------------------------------------------------
# 运行形态识别：源码运行（python run.py）/ pip 安装（pcleaner）/ 单文件 exe
# --------------------------------------------------------------------------
def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出的 exe 中。"""
    return bool(getattr(sys, "frozen", False))


def exe_dir() -> Optional[str]:
    """exe 所在目录；非冻结环境返回 None。"""
    return os.path.dirname(sys.executable) if is_frozen() else None


def bundle_dir() -> Optional[str]:
    """单文件 exe 解压出的临时资源目录（_MEIPASS）；非冻结环境返回 None。"""
    return getattr(sys, "_MEIPASS", None) if is_frozen() else None


def default_config_path() -> str:
    """按优先级定位默认配置文件，兼容源码/pip 安装/exe 三种运行形态。

    优先级：
      1) 环境变量 PROCESS_CLEANER_CONFIG
      2) 当前工作目录 configs/rules.json、rules.json（pip 安装后常用）
      3) exe 同目录 configs/rules.json（免安装版可自行改规则，优先于内置副本）
      4) exe 内置副本（PyInstaller --add-data 解包目录）
      5) 源码树 configs/rules.json（开发模式）
      6) 用户目录 ~/.process-cleaner/rules.json
    """
    candidates: List[str] = []
    env_path = os.environ.get("PROCESS_CLEANER_CONFIG")
    if env_path:
        candidates.append(env_path)

    cwd = os.getcwd()
    candidates.append(os.path.join(cwd, "configs", "rules.json"))
    candidates.append(os.path.join(cwd, "rules.json"))

    if is_frozen():
        candidates.append(os.path.join(exe_dir() or ".", "configs", "rules.json"))
        bundled = bundle_dir()
        if bundled:
            candidates.append(os.path.join(bundled, "configs", "rules.json"))

    candidates.append(os.path.join(PROJECT_ROOT, "configs", "rules.json"))
    candidates.append(os.path.join(os.path.expanduser("~"), ".process-cleaner", "rules.json"))

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    # 都不存在时回退到源码树路径，load_config 会给出 WARNING 并使用内置安全默认
    return os.path.join(PROJECT_ROOT, "configs", "rules.json")


def default_log_dir() -> str:
    """日志目录：exe 版写到 exe 同目录（无写权限时自动降级为仅控制台）。"""
    if is_frozen():
        return os.path.join(exe_dir() or ".", "logs")
    return os.path.join(PROJECT_ROOT, "logs")


def force_utf8_io() -> None:
    """Windows 控制台/管道默认编码可能是 GBK，会把中文与特殊字符打成问号。

    这里统一把标准输入/输出切换为 UTF-8，并对无法编码的字符做替换而不是抛异常。
    """
    for stream_name in ("stdout", "stderr", "stdin"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass  # 已被重定向且不可重配时保持原样


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须为正整数")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="process-cleaner",
        description="一键清理后台进程（默认预览，不会真的杀进程）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python -m process_cleaner --dry-run              预览待清理进程\n"
            "  python -m process_cleaner --execute              执行清理（需交互确认）\n"
            "  python -m process_cleaner --name foo.exe -x -y   清理指定进程且不再询问\n"
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-x", "--execute", action="store_true",
                      help="真正执行终止操作（默认仅预览）")
    mode.add_argument("-n", "--dry-run", action="store_true",
                      help="仅预览，不做任何终止（默认行为）")

    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过交互确认，仅对 --execute 生效")
    parser.add_argument("-c", "--config", default=None, metavar="PATH",
                        help="规则配置文件路径（默认按 环境变量 → 当前目录 → exe 同目录 → 内置 的顺序查找）")
    parser.add_argument("--name", action="append", default=[], metavar="NAME",
                        help="附加一个待清理的进程名，可重复传入")
    parser.add_argument("--pattern", action="append", default=[], metavar="REGEX",
                        help="附加一条进程名正则，可重复传入")
    parser.add_argument("--min-memory", type=float, default=None, metavar="MB",
                        help="只处理内存占用不低于该值的进程")
    parser.add_argument("--top", type=positive_int, default=None, metavar="N",
                        help="只处理内存占用最高的前 N 个")
    parser.add_argument("--log-dir", default=None, metavar="DIR",
                        help="日志目录（默认：源码模式为项目下 logs/，exe 模式为 exe 同目录 logs/；传空字符串关闭文件日志）")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="输出格式，json 便于脚本消费")
    parser.add_argument("--show-protected", action="store_true",
                        help="额外打印被白名单保护的进程样例")
    parser.add_argument("--use-cli-scanner", action="store_true",
                        help="强制使用命令行扫描（不使用 psutil）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    parser.add_argument("--version", action="version", version=f"process-cleaner {__version__}")
    return parser


def apply_cli_overrides(config: dict, args: argparse.Namespace) -> None:
    """把命令行参数合并进配置（命令行优先级最高）。"""
    match = config.setdefault("match", {})
    if args.name:
        merged = list(match.get("names", []) or [])
        merged.extend(args.name)
        match["names"] = merged
    if args.pattern:
        merged = list(match.get("name_patterns", []) or [])
        merged.extend(args.pattern)
        match["name_patterns"] = merged
    if args.min_memory is not None:
        match["min_memory_mb"] = args.min_memory
    if args.top is not None:
        match["max_count"] = args.top


def confirm(count: int, total_mb: float, err: bool = False) -> bool:
    """交互式二次确认；非交互环境（EOF）一律视为放弃。"""
    echo(err=err)
    echo(f"即将终止 {count} 个进程，预计释放 {total_mb:.1f} MB 内存。", err=err)
    echo("该操作不可撤销，请确认上面的清单无误。", err=err)
    echo('请输入 yes 继续执行（其它任意输入将取消）:', err=err)
    (sys.stderr if err else sys.stdout).flush()
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        echo("\n未获取到有效确认（非交互环境或用户中断），已取消。", err=err)
        return False
    return answer in {"yes", "y"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    force_utf8_io()
    parser = build_parser()
    args = parser.parse_args(argv)

    log_dir = args.log_dir if args.log_dir is not None else default_log_dir()
    config_path = args.config or default_config_path()

    # JSON 模式：日志与提示走 stderr，stdout 只输出纯 JSON，便于脚本解析
    json_mode = args.format == "json"
    setup_logging(log_dir or None, verbose=args.verbose,
                  stream=sys.stderr if json_mode else None)

    try:
        config = load_config(config_path)
        apply_cli_overrides(config, args)

        dry_run = not args.execute
        if dry_run:
            logger.info("预览模式（dry-run）：不会终止任何进程")

        processes, source = scan_processes(prefer="cli" if args.use_cli_scanner else "auto")
        if not processes:
            echo("未能获取到任何进程信息，请检查系统权限后重试。", err=json_mode)
            return EXIT_ERROR
        logger.info("扫描到 %d 个进程（来源: %s）", len(processes), source)

        engine = RuleEngine(config)
        result = engine.evaluate(processes, source=source)
        log_scan_result(logger, result)

        # ---- 输出待终止清单 ----
        if args.format == "json":
            if dry_run or not result.kill:
                echo(render_json(result, dry_run=dry_run))
            else:
                echo(render_processes(result.kill, "待终止进程清单"), err=True)
        else:
            echo()
            echo(render_processes(result.kill, "待终止进程清单" if not dry_run else "预览：待终止进程清单"))
            if args.show_protected:
                echo()
                echo(render_protected(result))
            echo(render_summary(result, dry_run=True))

        if not result.kill:
            echo(err=json_mode)
            has_match_rule = bool(
                (config.get("match", {}) or {}).get("names")
                or (config.get("match", {}) or {}).get("name_patterns")
                or (config.get("match", {}) or {}).get("cmdline_patterns")
            )
            if has_match_rule:
                # 没有候选可能来自名称、内存阈值或白名单过滤。
                echo("没有符合清理条件的进程（未匹配、低于内存阈值或受白名单保护）。", err=json_mode)
                echo("如需查看被保护的进程及原因，可追加 --show-protected。", err=json_mode)
            else:
                echo("没有命中任何清理规则的进程，无需处理。", err=json_mode)
                echo("提示：请先在 configs/rules.json 的 match 中配置清理目标，或使用 --name/--pattern 传入。", err=json_mode)
            return EXIT_NOTHING

        if dry_run:
            echo(err=json_mode)
            echo("这是预览结果。确认无误后，追加 --execute 才会真正终止进程。", err=json_mode)
            return EXIT_OK

        # ---- 执行模式：二次确认 ----
        if not args.yes and not confirm(len(result.kill), result.total_memory_mb, err=json_mode):
            logger.info("用户取消操作")
            echo("已取消，未终止任何进程。", err=json_mode)
            if json_mode:
                echo(render_json(result, dry_run=True))
            return EXIT_ABORT

        kill_cfg = config.get("kill", {}) or {}
        stats = terminate_all(
            result.kill,
            grace_period=float(kill_cfg.get("grace_period_sec", 5) or 0),
            force=bool(kill_cfg.get("force", False)),
        )
        log_action(logger, result, stats, dry_run=False)

        if args.format == "json":
            echo(render_json(result, stats, dry_run=False))
        else:
            echo(render_summary(result, stats, dry_run=False))
        return EXIT_OK if not stats.failed else EXIT_ERROR

    except KeyboardInterrupt:
        echo("\n已被用户中断（Ctrl+C），未做进一步操作。", err=json_mode)
        return EXIT_ABORT
    except PermissionError as exc:
        echo(f"权限不足: {exc}\n请以管理员/root 权限重新运行。", err=json_mode)
        return EXIT_ERROR
    except Exception as exc:  # 兜底：任何异常都转成友好提示
        logger.exception("运行过程中发生未预期错误: %s", exc)
        echo(f"发生未预期错误: {exc}", err=json_mode)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
