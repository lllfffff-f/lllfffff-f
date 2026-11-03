"""规则模块：配置加载 + 白名单/匹配规则过滤。

职责：
1. 加载并校验 configs/rules.json（缺失或非法时回退到内置默认规则，绝不崩溃）；
2. 依据「保护规则」和「匹配规则」对每个进程做出 Decision（终止 / 保护 / 跳过）。

防误杀的核心设计：**默认不杀任何进程**。
一个进程只有在「显式命中匹配规则」且「未命中任何保护规则」时才会被列入终止清单，
即白名单是"一票否决"，匹配清单是"显式授权"，两者缺一不可。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List, Optional, Sequence

from .models import Decision, ProcessInfo, ScanResult
from .scanner import self_pid_tree

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 内置兜底规则：即使用户配置文件损坏，也保证系统关键进程不被触碰
# ---------------------------------------------------------------------------
BUILTIN_SYSTEM_PROTECTED = {
    # Windows 核心
    "system", "system idle process", "smss.exe", "csrss.exe", "wininit.exe",
    "winlogon.exe", "services.exe", "lsass.exe", "lsaiso.exe", "svchost.exe",
    "dwm.exe", "explorer.exe", "sihost.exe", "fontdrvhost.exe", "ctfmon.exe",
    "taskhostw.exe", "runtimebroker.exe", "audiodg.exe", "conhost.exe",
    "securityhealthservice.exe", "securityhealthsystray.exe", "searchindexer.exe",
    "spoolsv.exe", "wudfhost.exe", "unsecapp.exe", "wmiprvse.exe",
    "trustedinstaller.exe", "dllhost.exe", "msmpeng.exe", "nissrv.exe",
    "mpdefendercoreservice.exe", "shellinfrastructure.exe", "startmenuexperiencehost.exe",
    "searchhost.exe", "textinputhost.exe", "applicationframehost.exe",
    "lockapp.exe", "userinit.exe", "wsappx.exe", "wlanext.exe",
    # Linux / macOS 核心
    "init", "systemd", "kthreadd", "launchd", "kernel_task", "dbus-daemon",
    "loginwindow", "windowserver", "sshd", "udevd", "xorg",
}

# 编辑器 / IDE / 终端：清理后台进程时最容易误伤自己的工作区
BUILTIN_EDITORS_TERMINALS = {
    # 编辑器与 IDE
    "code.exe", "code", "codehelper", "devenv.exe", "idea64.exe", "idea.exe",
    "pycharm64.exe", "pycharm.exe", "webstorm64.exe", "goland64.exe",
    "clion64.exe", "rider64.exe", "java", "java.exe", "javaw.exe",
    "notepad++.exe", "sublime_text.exe", "vim", "nvim", "emacs", "cursor.exe",
    "windsurf.exe", "trae.exe", "workbuddy.exe",
    # 终端与 shell
    "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe",
    "openconsole.exe", "bash.exe", "bash", "zsh", "fish", "sh", "wsl.exe",
    "wslservice.exe", "git.exe", "git", "conemu64.exe", "wt.exe",
    # 本工具的宿主进程
    "python.exe", "pythonw.exe", "python", "python3", "py.exe",
}

BUILTIN_PROTECTED_USERS = {"system", "local service", "network service", "root"}

DEFAULT_CONFIG: Dict = {
    "version": 1,
    "protect": {
        "names": [],            # 用户自定义附加保护（进程名，忽略大小写）
        "name_patterns": [],    # 用户自定义附加保护（正则）
        "path_prefixes": [],    # 可执行文件目录前缀
        "users": [],            # 运行账户
        "self_and_ancestors": True,   # 保护自身与父进程链
        "system_critical": True,      # 启用内置系统关键进程清单
        "editors_and_terminals": True,# 启用内置编辑器/终端清单
    },
    "match": {
        "names": [],            # 精确进程名（忽略大小写）
        "name_patterns": [],    # 正则匹配进程名
        "cmdline_patterns": [], # 正则匹配命令行（仅 psutil 模式可用）
        "min_memory_mb": 0,     # 内存下限，0 表示不限制
        "max_count": 200,       # 单次最多终止数量，兜底防爆
        "excluded_names": [],   # 匹配清单里的例外（二次排除）
    },
    "kill": {
        "grace_period_sec": 5,  # 优雅退出等待时间
        "force": False,         # True 时直接强制终止
        "dry_run_default": True,# 不带 --yes 时默认预览
    },
}


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------
def _deep_merge(base: Dict, override: Dict) -> Dict:
    """递归合并，override 覆盖 base，未知键保留。"""
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Optional[str] = None) -> Dict:
    """加载配置文件；文件缺失/非法时回退默认配置并记录警告。"""
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝，避免污染默认值
    if not path:
        return config
    if not os.path.isfile(path):
        logger.warning("配置文件不存在（%s），使用内置默认规则", path)
        return config
    try:
        with open(path, "r", encoding="utf-8") as fh:
            user_config = json.load(fh)
        if not isinstance(user_config, dict):
            raise ValueError("配置文件根节点必须是 JSON 对象")
        config = _deep_merge(config, user_config)
        logger.info("已加载配置文件: %s", os.path.abspath(path))
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("配置文件解析失败（%s），使用内置默认规则", exc)
    return config


def compile_patterns(patterns: Sequence[str]) -> List["re.Pattern"]:
    """编译正则；单条非法正则只跳过该条，不影响整体。"""
    compiled: List[re.Pattern] = []
    for pattern in patterns or []:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error as exc:
            logger.warning("忽略非法正则 %r: %s", pattern, exc)
    return compiled


# ---------------------------------------------------------------------------
# 过滤判定
# ---------------------------------------------------------------------------
class RuleEngine:
    """依据配置对每个进程做保护 / 匹配判定。"""

    def __init__(self, config: Dict) -> None:
        self.config = config
        protect = config.get("protect", {}) or {}
        match = config.get("match", {}) or {}

        self.protect_names = {
            str(n).lower() for n in protect.get("names", []) or []
        }
        self.protect_patterns = compile_patterns(protect.get("name_patterns", []))
        self.protect_paths = [
            str(p).lower() for p in protect.get("path_prefixes", []) or []
        ]
        self.protect_users = {
            str(u).lower() for u in protect.get("users", []) or []
        }
        self.use_system_critical = bool(protect.get("system_critical", True))
        self.use_editors = bool(protect.get("editors_and_terminals", True))
        self.protect_self = bool(protect.get("self_and_ancestors", True))
        self._self_pids = self_pid_tree() if self.protect_self else set()

        self.match_names = {str(n).lower() for n in match.get("names", []) or []}
        self.match_patterns = compile_patterns(match.get("name_patterns", []))
        self.match_cmdline = compile_patterns(match.get("cmdline_patterns", []))
        self.min_memory_mb = float(match.get("min_memory_mb", 0) or 0)
        self.max_count = int(match.get("max_count", 200) or 200)
        self.excluded_names = {
            str(n).lower() for n in match.get("excluded_names", []) or []
        }

    # ---------------- 保护规则 ----------------
    def protection_reason(self, proc: ProcessInfo) -> Optional[str]:
        """命中保护规则则返回原因文本，否则返回 None。"""
        if self.protect_self and proc.pid in self._self_pids:
            return "本工具自身或其父进程（防止自杀）"

        name = (proc.name or "").lower()
        display = (proc.display_name or "").lower()

        if self.use_system_critical and (
            name in BUILTIN_SYSTEM_PROTECTED or display in BUILTIN_SYSTEM_PROTECTED
        ):
            return "系统关键进程（内置白名单）"
        if self.use_editors and (
            name in BUILTIN_EDITORS_TERMINALS or display in BUILTIN_EDITORS_TERMINALS
        ):
            return "编辑器/终端进程（内置白名单）"
        if name in self.protect_names or display in self.protect_names:
            return "用户自定义进程白名单"
        if name in self.excluded_names or display in self.excluded_names:
            return "匹配规则中的例外清单"
        for pattern in self.protect_patterns:
            if pattern.search(name) or pattern.search(display):
                return f"用户自定义保护正则: {pattern.pattern}"
        if proc.exe:
            exe_lower = proc.exe.lower().replace("\\", "/")
            for prefix in self.protect_paths:
                if exe_lower.startswith(prefix.lower().replace("\\", "/")):
                    return f"受保护路径前缀: {prefix}"
        if proc.username and proc.username.lower() in self.protect_users:
            return f"受保护账户: {proc.username}"
        if proc.username and proc.username.lower() in BUILTIN_PROTECTED_USERS:
            return f"系统账户进程: {proc.username}"
        return None

    # ---------------- 匹配规则 ----------------
    def is_match(self, proc: ProcessInfo) -> Optional[str]:
        """命中清理规则则返回命中的规则文本，否则返回 None。"""
        name = (proc.name or "").lower()
        display = (proc.display_name or "").lower()

        for target in self.match_names:
            if name == target or display == target:
                return f"进程名匹配: {target}"
        for pattern in self.match_patterns:
            if pattern.search(name) or pattern.search(display):
                return f"进程名正则: {pattern.pattern}"
        for pattern in self.match_cmdline:
            if proc.cmdline and pattern.search(proc.cmdline):
                return f"命令行正则: {pattern.pattern}"
        return None

    # ---------------- 主入口 ----------------
    def evaluate(self, processes: Sequence[ProcessInfo], source: str = "unknown") -> ScanResult:
        """返回 ScanResult：kill 列表按内存降序，并受 max_count 截断保护。"""
        result = ScanResult(scanned=len(processes), source=source)

        for proc in processes:
            reason = self.protection_reason(proc)
            if reason:
                result.protected.append(Decision(proc, False, reason, "protect"))
                continue
            hit = self.is_match(proc)
            if not hit:
                result.skipped.append(Decision(proc, False, "未命中任何清理规则", "filter"))
                continue
            if self.min_memory_mb and proc.memory_mb < self.min_memory_mb:
                result.skipped.append(
                    Decision(proc, False, f"内存 {proc.memory_mb:.1f}MB 低于阈值 {self.min_memory_mb:.1f}MB", "filter")
                )
                continue
            result.kill.append(proc)
            result.protected.append(Decision(proc, True, hit, "match"))  # 便于日志追溯

        result.kill.sort(key=lambda p: p.memory_mb, reverse=True)
        if self.max_count > 0 and len(result.kill) > self.max_count:
            logger.warning(
                "命中 %d 个进程，超过 max_count=%d，仅保留内存占用最高的 %d 个",
                len(result.kill), self.max_count, self.max_count,
            )
            result.kill = result.kill[: self.max_count]
        return result
