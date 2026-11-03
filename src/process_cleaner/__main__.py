"""支持 `python -m process_cleaner` 直接运行，同时作为 PyInstaller 的打包入口。

说明：
- 以包形式运行（python -m / pcleaner）时，相对导入可用；
- 被 PyInstaller 冻结为单文件 exe 时，本文件是作为顶层脚本执行的，
  没有父包，相对导入会抛 ImportError —— 此时回退到绝对导入。
"""

import sys

try:
    from .cli import main                 # python -m process_cleaner
except ImportError:                       # pragma: no cover - 仅冻结环境触发
    from process_cleaner.cli import main  # 单文件 exe 入口

if __name__ == "__main__":
    sys.exit(main())
