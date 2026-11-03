# process-cleaner · 一键清理后台进程

一个安全优先的命令行工具：扫描系统后台进程，按白名单排除系统关键进程、编辑器与终端进程，
先预览、再确认、最后优雅终止，全过程留日志。

> ⚠️ 本工具默认**不会终止任何进程**。只有显式传入 `--execute` 才会真正执行，且执行前必须二次确认。

---

## 1. 目录结构

```
process-cleaner/
├── pyproject.toml              # pip 安装配置（setuptools + src-layout），声明 pcleaner 命令
├── build_exe.py                # 一键打包脚本（PyInstaller → Windows 单文件 exe）
├── run.py                      # 便捷启动脚本（无需设置 PYTHONPATH）
├── requirements.txt            # 运行期依赖说明（默认零依赖）
├── requirements-dev.txt        # 开发/测试/打包依赖说明
├── configs/
│   └── rules.json              # 清理规则与排除规则配置
├── src/process_cleaner/
│   ├── __init__.py             # 版本声明
│   ├── __main__.py             # python -m 入口，同时作为 PyInstaller 打包入口
│   ├── models.py               # 数据模型（ProcessInfo / Decision / ScanResult）
│   ├── scanner.py              # 进程扫描
│   ├── rules.py                # 配置加载 + 白名单/匹配规则过滤
│   ├── terminator.py           # 进程终止执行
│   ├── logging_setup.py        # 日志输出与落盘
│   ├── report.py               # 结果渲染（表格 / JSON）
│   └── cli.py                  # 参数解析与流程编排、运行形态识别
├── tests/                      # 单元测试（unittest，零依赖）
├── docs/
│   └── DESIGN.md               # 模块职责、实现思路与防误杀方案
├── dist/                       # 打包产物（gitignore）：process-cleaner.exe + configs/
└── logs/                       # 运行日志（自动生成，已 gitignore）
```

## 2. 安装（三种方式，任选其一）

### 方式 A：免安装 exe（推荐给不装 Python 的机器）

直接下载 `dist/process-cleaner.exe`（单文件，约 8 MB），双击或在终端运行即可，
**无需预装 Python、无需配置任何依赖**。详见 [第 3 节](#3-打包-windows-单文件-exe)。

### 方式 B：pip 安装（推荐开发者/长期使用者）

```bash
cd process-cleaner
python -m venv .venv
.venv\Scripts\activate                 # PowerShell: .venv\Scripts\Activate.ps1

pip install -e .                       # 开发模式（改代码立即生效，零依赖）
pip install -e ".[full]"               # 开发模式 + psutil 增强（推荐）
pip install .                          # 普通安装
pip install ".[full]"                  # 普通安装 + psutil 增强

pcleaner --version                     # 安装后即可用 pcleaner 命令
```

extras 说明（见 `pyproject.toml`）：

| extra | 内容 | 用途 |
| --- | --- | --- |
| （无） | 无 | 零依赖运行，离线可装；内存与路径信息走 tasklist / ps 回退 |
| `full` | `psutil>=5.9.0` | 准确的 RSS 内存与可执行文件路径（启用 `path_prefixes` 规则） |
| `build` | `pyinstaller>=6.0` | 打包 exe 所需 |
| `dev` | psutil + pyinstaller + pytest | 完整开发环境 |

### 方式 C：直接用源码跑（不改环境）

```bash
python run.py --version                                # 无需 pip install
PYTHONPATH=src python -m process_cleaner --version      # Linux/macOS
set PYTHONPATH=src && python -m process_cleaner --version  # Windows CMD
```

要求：Python >= 3.9（Windows / Linux / macOS 均可）。三种方式的**功能与命令行参数完全一致**。

> pip 安装后若未指定 `--config`，配置按以下顺序查找：
> `环境变量 PROCESS_CLEANER_CONFIG` → 当前目录 `configs/rules.json`、`rules.json`
> → exe 同目录 `configs/rules.json`（仅 exe）→ exe 内置副本（仅 exe）
> → 源码树 `configs/rules.json` → `~/.process-cleaner/rules.json`。
> 都不存在时回退到内置安全默认（默认不清理任何进程）。

## 3. 打包 Windows 单文件 exe

```bash
# 1) 准备构建环境（Python 3.12 64 位；本项目 exe 即用该版本构建）
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[build]"       # 或 pip install -e ".[dev]"（含 psutil）

# 2) 一键打包
python build_exe.py             # 加 --clean 可先清理上次的 dist/ 与 build/

# 3) 产物
dist\process-cleaner.exe        # 单文件 exe（约 8 MB），可直接拷贝走，无需 Python
dist\configs\rules.json         # 可编辑的规则配置，优先级高于 exe 内置副本
```

打包细节：

- 入口：`src/process_cleaner/__main__.py`（已兼容冻结环境的绝对导入）。
- 资源：`configs/rules.json` 通过 `--add-data` 打进 exe，运行时解压到 `_MEIxxxxxx` 临时目录；
  同时复制一份到 `dist/configs/rules.json`，改这份即可自定义规则，无需重新打包。
- 依赖：exe 内置 Python 解释器与 psutil（可选依赖），目标机器零依赖；
  构建环境没装 psutil 也能打包，程序会自动回退 tasklist / ps 模式。
- 日志：exe 运行时写在**exe 同目录**的 `logs\` 下；无写权限时自动降级为仅控制台输出。
- 产物路径：`process-cleaner/dist/process-cleaner.exe`（Git 忽略 `dist/`、`build/`、`*.spec`）。

## 4. 用法

> 以下示例用 `python run.py`，三种运行方式参数完全一致：
> exe 版把 `python run.py` 换成 `process-cleaner.exe`，pip 版换成 `pcleaner`。

```bash
# 预览（默认行为，绝不杀进程）
python run.py --dry-run

# 查看被白名单保护的进程样例，核对规则是否合理
python run.py --dry-run --show-protected

# 临时指定目标：按进程名 / 正则 / 内存阈值
python run.py --dry-run --name chrome.exe --name msedge.exe --min-memory 200

# 真正执行（交互式二次确认）
python run.py --execute --name chrome.exe

# 执行且不再询问（仅限已确认清单的场景，如计划任务）
python run.py --execute --yes --name chrome.exe

# 只看内存占用最高的 5 个候选（--top 必须为正整数）
python run.py --dry-run --pattern '.' --top 5

# 输出 JSON，便于其它脚本消费
python run.py --dry-run --format json

# 使用自定义配置文件 / 关闭文件日志
python run.py --config configs/rules.local.json --log-dir ""

# 调试模式（输出保护原因等 DEBUG 日志）
python run.py --dry-run -v
```

等价入口（需先设置模块搜索路径）：

```bash
# Windows PowerShell
$env:PYTHONPATH = "src"; python -m process_cleaner --dry-run
# Windows CMD
set PYTHONPATH=src && python -m process_cleaner --dry-run
# Linux / macOS
PYTHONPATH=src python -m process_cleaner --dry-run
```

### 退出码

`--format json` 在预览、正常执行、部分终止失败以及用户取消时，stdout 只输出一个 JSON 对象。
执行前的候选清单、确认提示和诊断信息写入 stderr；取消时 `dry_run` 为 `true`，不包含执行结果。
扫描失败等无法生成结果的错误只写 stderr，stdout 为空，调用脚本应同时检查退出码。

强制终止也只处理候选清单中的 PID，不自动连带终止子进程。命令失败或无法查询进程状态时
记为失败；发出强制终止命令后会轮询核实退出（等待窗口 5 秒，单次系统查询可能额外耗时）。

| 退出码 | 含义 |
| --- | --- |
| 0 | 正常结束 |
| 1 | 发生错误（含部分进程终止失败） |
| 2 | 无匹配进程，无需处理 |
| 3 | 用户取消 / Ctrl+C 中断 |

## 5. 配置说明（`configs/rules.json`）

```jsonc
{
  "protect": {                    // 保护规则（白名单），命中任一条件即"一票否决"
    "system_critical": true,      // 内置系统关键进程清单（svchost/lsass/explorer...）
    "editors_and_terminals": true,// 内置编辑器/终端清单（Code/cmd/powershell/bash...）
    "self_and_ancestors": true,   // 保护本工具自身及其父进程链
    "names": ["WeChat.exe"],      // 自定义进程名
    "name_patterns": ["^NVIDIA"], // 自定义正则
    "path_prefixes": ["C:\\Windows\\"], // 路径前缀（需 psutil）
    "users": []                   // 运行账户（如 "SYSTEM"）
  },
  "match": {                      // 清理规则：只有显式命中才会被清理
    "names": ["chrome.exe"],      // 精确进程名（忽略大小写）
    "name_patterns": ["^.*helper$"],
    "cmdline_patterns": [],       // 命令行正则（需 psutil）
    "min_memory_mb": 100,         // 内存下限
    "max_count": 50,              // 单次最多终止数量（防爆兜底）
    "excluded_names": []          // 匹配清单中的例外
  },
  "kill": {
    "grace_period_sec": 5,        // 优雅退出等待秒数，超时才升级强制终止
    "force": false,               // true = 直接强制终止，跳过宽限期
    "dry_run_default": true       // 不带 --execute 时始终预览
  }
}
```

- 配置文件缺失或 JSON 非法 → 记录 WARNING 并回退到内置安全默认（**默认不清理任何进程**）。
- 建议把个人化规则放在 `configs/rules.local.json`（已被 .gitignore 忽略），通过 `--config` 指定。

## 6. 注意事项（请务必阅读）

1. **默认零风险**：`match` 默认为空，未配置清理目标时工具不会终止任何进程。
2. **先预览后执行**：务必先用 `--dry-run` 核对清单，再追加 `--execute`。
3. **管理员权限**：终止他人/系统级进程需要管理员（Windows）或 root（Linux/macOS）权限；
   权限不足时只记录 ERROR 并继续处理其它进程，不会中断或崩溃。
4. **不可撤销**：被终止的进程若有未保存数据会直接丢失，请提前保存工作内容。
5. **不要清理数据库、虚拟机、容器、下载/备份类进程**，它们被强杀后可能造成数据损坏。
6. **编辑器与终端默认受保护**（Code/cmd/PowerShell/WindowsTerminal/bash 等），
   但仍建议执行前确认当前工作区已保存。
7. 日志默认写入 `logs/cleaner-YYYYMMDD.log`（exe 版在 exe 同目录），含被保护进程的原因，便于事后审计。
8. exe 为单文件但**首次启动需自解压到临时目录**（约 1-2 秒），属正常现象；
   杀毒软件可能误报 PyInstaller 打包产物，需自行加入信任。

## 7. 测试

```bash
python -m unittest discover -s tests -v     # 零依赖，离线可跑
pytest tests                                 # 若已安装 pytest
```

终止与执行模式的回归测试使用模拟系统调用，不会终止真实进程。
本次审查范围、修复与后续事项见 [审查记录](docs/REVIEW.md)。

## 8. 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 中文进程名显示为问号 | 已修复：扫描器按系统 ANSI 代码页解码命令行输出 |
| 提示"全部被白名单保护" | 目标进程命中了保护规则，追加 `--show-protected` 查看具体原因 |
| 部分 PID 终止失败 | 多为权限不足，请以管理员/root 重新运行 |
| 装了 psutil 却仍显示 tasklist | 使用了 `--use-cli-scanner`，或 psutil 枚举异常自动回退 |
| exe 报 `ImportError: attempted relative import` | 已修复：`__main__.py` 兼容冻结环境；请确保用最新版重新打包 |
| pip 安装后找不到规则文件 | 用 `-c` 指定，或把 `rules.json` 放在当前目录 / 设置 `PROCESS_CLEANER_CONFIG` |
| `pip install` 找不到包 | 需能访问 PyPI；本项目零硬依赖，离线环境用方式 C（源码直跑） |

## 9. 验证清单（发布前自检）

```bash
# 1) 源码模式
python run.py --version && python -m unittest discover -s tests

# 2) pip 安装模式
pip install -e ".[full]"
pcleaner --version                       # 期望输出 process-cleaner 1.1.0
pcleaner --dry-run --name chrome.exe     # 期望输出预览清单，且不终止任何进程

# 3) exe 模式
python build_exe.py
dist\process-cleaner.exe --version       # 期望输出 process-cleaner 1.1.0
dist\process-cleaner.exe --dry-run -v    # 期望看到 已加载配置文件 与 扫描来源=psutil
echo n | dist\process-cleaner.exe --execute --name chrome.exe   # 期望"已取消，未终止任何进程"
```

## 10. Git 开发流程（feature 分支）

```bash
git checkout -b feature/pip-and-exe      # 在 feature 分支开发
git add <file> && git commit -m "feat(packaging): ..."
git checkout main && git merge --no-ff feature/pip-and-exe
```

提交规范：按功能拆分语义化 commit，如 `feat(scanner)` / `feat(rules)` / `fix(cli)` / `docs` / `test` / `chore`。
