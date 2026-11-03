#!/usr/bin/env python
"""项目根目录下的便捷启动脚本：无需设置 PYTHONPATH 即可运行。

    python run.py --dry-run
    python run.py --execute
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from process_cleaner.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
