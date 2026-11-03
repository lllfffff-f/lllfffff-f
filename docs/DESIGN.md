# process-cleaner 设计说明

## 1. 模块职责

| 模块 | 职责 | 不做什么 |
| --- | --- | --- |
| `models.py` | 定义 `ProcessInfo`（进程快照，frozen 不可变）、`Decision`（过滤判定）、`ScanResult`（扫描+过滤结果） | 不做 IO、不做判断 |
| `scanner.py` | 枚举系统进程 → 统一 `ProcessInfo` 列表；psutil 优先，回退 `tasklist`(Win) / `ps`(POSIX) | 不做过滤、不终止进程 |
| `rules.py` | 加载校验配置文件；按保护规则与匹配规则做判定，输出 `ScanResult` | 不接触进程 |
| `terminator.py` | 优雅终止 → 超时强杀；处理权限不足/进程已退出等异常并返回统计 | 不做决策 |
| `logging_setup.py` | 控制台 + 按天滚动文件日志；记录扫描结果、保护原因、终止结果 | 不参与业务 |
| `report.py` | 表格/JSON 渲染，处理中文列宽对齐 | 不做判断 |
| `cli.py` | 参数解析、流程编排、二次确认、退出码、全局异常兜底 | 不含具体算法 |

依赖方向单向：`cli → {rules, scanner, terminator, report, logging} → models`，
模块之间不反向依赖，可单独测试。

## 2. 关键实现思路

### 2.1 扫描：双引擎 + 单进程容错
- 优先 `psutil`（可选依赖）拿准确 RSS 与可执行文件路径；未安装时回退命令行。
- Windows 的 `tasklist` 输出是系统 ANSI 代码页（简体中文为 GBK）字节，
  若按 UTF-8 解码会把中文进程名变成替换字符 → 实现 `_decode()` 依次尝试
  `utf-8 → mbcs/gbk → latin-1`，保证永不抛异常（该问题已在 `fix(scanner)` 中修复）。
- 遍历中对每个进程单独 `try/except`，`NoSuchProcess / AccessDenied / ZombieProcess`
  一律跳过，单个进程读取失败不影响整体扫描。

### 2.2 过滤：白名单一票否决 + 匹配清单显式授权
判定顺序（短路）：

1. 自身与父进程链 → 保护
2. 内置系统关键进程清单 → 保护
3. 内置编辑器/终端清单 → 保护
4. 用户自定义 names / 正则 / 路径前缀 / 账户 → 保护
5. 未命中 `match` 规则 → 跳过
6. 命中但低于 `min_memory_mb` → 跳过
7. 其余 → 列入待终止清单，并按内存降序、`max_count` 截断

即"**白名单优先于一切**"：即使规则同时把某进程写进了 `match` 和 `protect`，
结果依然是保护，杜绝配置冲突导致的误杀。

### 2.3 终止：先礼后兵
- 先发优雅退出信号（Windows `taskkill`，POSIX `SIGTERM`），轮询等待 `grace_period_sec`；
- 超时仍存活才升级为 `taskkill /F /PID <pid>` 或 `SIGKILL`，不使用 `/T`，避免终止未经过规则检查的子进程；
- `force=true` 时跳过宽限期；
- 强制终止后轮询核实进程退出，Windows 命令返回非零或状态查询失败均记为失败；
- Windows 以 CSV 的 PID 列判断进程是否存在，POSIX 区分进程不存在与无权查询；
- 等待使用单调时钟，拒绝 PID 0 和负数，避免 POSIX 进程组信号；
- 每一步都返回 `success / gone / failed` 状态，批量执行中**单进程失败不中断**，
  最终汇总成功数、已退出数、失败 PID 与释放内存。

### 2.4 日志：可审计
每次运行写入 `logs/cleaner-YYYYMMDD.log`（DEBUG 级），
控制台只输出 INFO 及以上，避免刷屏；DEBUG 日志会记录每个被保护进程的**具体原因**，
便于事后回答"为什么这个进程没被清掉 / 为什么被清掉"。

## 3. 防误杀方案（六道防线）

1. **默认不杀**：内置默认配置 `match` 为空，未配置目标时命中数为 0。
2. **内置系统关键进程白名单**：svchost / lsass / csrss / winlogon / explorer /
   services / dwm / spoolsv / audiodg / systemd / launchd 等约 60 项。
3. **编辑器与终端白名单**：Code、devenv、JetBrains 系列、cmd、PowerShell、
   WindowsTerminal、bash/zsh、wsl、git 等，避免杀掉自己的工作区和 shell。
4. **自身进程树保护**：本进程及其所有祖先 PID 永不进入清单（防自杀、防杀掉启动它的终端）。
5. **系统账户与系统目录保护**：`SYSTEM / LOCAL SERVICE / NETWORK SERVICE / root`
   账户、`C:\Windows\` 等目录前缀下的进程一律保护。
6. **预览 + 二次确认 + 数量上限**：默认 dry-run；执行模式必须打印完整清单并交互确认
   （`--yes` 才跳过）；`max_count` 兜底限制单次影响面；`min_memory_mb` 避免误伤小进程。

补充约束：终止采用优雅优先，给进程保存数据的机会；
配置文件损坏时回退到"最保守"默认值，而不是"最激进"行为。

## 4. 异常处理矩阵

| 场景 | 处理 | 结果 |
| --- | --- | --- |
| 配置文件缺失/非法 JSON | WARNING + 回退默认规则 | 继续运行 |
| 单条正则非法 | WARNING + 跳过该条 | 继续运行 |
| 进程读取失败（权限/已退出） | DEBUG 记录，跳过 | 继续扫描 |
| 扫描命令不存在/超时 | WARNING，返回空列表 | 友好报错退出码 1 |
| 终止时权限不足 | ERROR 记录，跳过 | 继续处理其它进程 |
| 终止目标已退出 | 记为 `gone` | 计入成功 |
| 优雅终止超时 | 升级强制终止 | 记录 WARNING |
| 用户 Ctrl+C / 取消确认 | 提示并退出 | 退出码 3，未做任何终止 |
| 其它未预期异常 | `logger.exception` + 友好提示 | 退出码 1，无堆栈恐慌 |

## 5. 三种运行形态（v1.1.0）

同一份代码支持三种分发方式，命令行参数与功能完全一致：

| 形态 | 入口 | 依赖 | 特点 |
| --- | --- | --- | --- |
| 源码直跑 | `python run.py` / `python -m process_cleaner` | 零（可选 psutil） | 开发用，改代码即生效 |
| pip 安装 | `pcleaner`（console script） | 零；`[full]` 带 psutil | 由 `pyproject.toml` 的 `[project.scripts]` 生成 |
| 单文件 exe | `dist/process-cleaner.exe` | 零（解释器与 psutil 已内置） | PyInstaller `--onefile`，目标机器无需 Python |

### 5.1 打包实现要点

- **入口兼容性**：PyInstaller 以顶层脚本方式执行 `__main__.py`，相对导入会失败；
  因此 `__main__.py` 采用「先相对导入、失败则绝对导入」的双分支写法，
  同时保证 `python -m process_cleaner` 与 exe 都能启动。
- **资源文件**：`configs/rules.json` 通过 `--add-data "configs/rules.json;configs"` 打进 exe，
  运行期出现在 `sys._MEIPASS` 临时目录；为避免用户改规则要重新打包，
  构建脚本额外复制一份到 `dist/configs/rules.json`。
- **配置解析链**（`cli.default_config_path()`）：
  环境变量 → 当前目录 → exe 同目录 → exe 内置 → 源码树 → `~/.process-cleaner/rules.json`，
  外部配置优先于内置副本，全部缺失时回退内置安全默认。
- **日志目录**（`cli.default_log_dir()`）：exe 模式写在 exe 同目录 `logs\`，
  无写权限时 `setup_logging` 自动降级为仅控制台输出。
- **依赖边界**：psutil 是 optional-dependency，pip 安装不强制；
  但打包时若构建环境存在 psutil 则一并内置，使 exe 具备完整能力。

## 6. 后续可优化方向

- 进程树关联展示（父子关系），避免留下孤儿进程；
- 按 CPU 时间 / 运行时长补充清理维度；
- 交互式 TUI 勾选清单（`--pick`）；
- 规则配置文件支持 YAML 与目录级继承；
- 增加"回收站"机制：先 suspend 再 kill，支持 `undo`。
