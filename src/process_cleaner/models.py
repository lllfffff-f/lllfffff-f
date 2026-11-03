"""数据模型：进程快照与过滤结果。

只做数据结构定义，不含任何 IO 或业务逻辑，方便被其它模块与测试复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ProcessInfo:
    """一次扫描得到的进程快照（不可变，避免被下游误改）。"""

    pid: int
    name: str
    memory_mb: float = 0.0
    exe: Optional[str] = None
    username: Optional[str] = None
    cmdline: Optional[str] = None

    @property
    def display_name(self) -> str:
        """统一显示名：优先可执行文件路径的文件名，回退到进程名。"""
        if self.exe:
            return self.exe.replace("\\", "/").rsplit("/", 1)[-1]
        return self.name


@dataclass
class Decision:
    """过滤判定结果：一个进程最终是被选中终止，还是被保护/跳过。"""

    process: ProcessInfo
    kill: bool
    reason: str = ""
    rule: str = ""

    def __post_init__(self) -> None:
        # reason 为空时给出可读兜底，保证日志与表格永远有解释。
        if not self.reason:
            self.reason = "命中清理规则" if self.kill else "未命中任何清理规则"
        self.rule = self.rule or ("match" if self.kill else "filter")


@dataclass
class ScanResult:
    """扫描 + 过滤的完整结果，供 CLI 渲染与日志记录。"""

    scanned: int = 0
    kill: list = field(default_factory=list)      # list[ProcessInfo]
    protected: list = field(default_factory=list)  # list[Decision]，记录被保护的原因
    skipped: list = field(default_factory=list)    # list[Decision]，未命中清理规则
    source: str = "unknown"                        # 扫描来源：psutil / tasklist / ps

    @property
    def total_memory_mb(self) -> float:
        return sum(p.memory_mb for p in self.kill)
