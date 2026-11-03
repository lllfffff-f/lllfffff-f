#!/usr/bin/env python
"""一键打包脚本：用 PyInstaller 生成 Windows 单文件 exe。

用法（建议在已安装依赖的环境中执行）：
    python build_exe.py                 # 默认生成 dist/process-cleaner.exe
    python build_exe.py --clean         # 清理上次构建产物后重新打包

产物与资源：
  * 可执行文件：dist/process-cleaner.exe（单文件，无需安装 Python）
  * 资源文件：configs/rules.json 会以 --add-data 打进 exe，
    同时复制一份到 dist/configs/rules.json，用户可直接改这份外部配置（优先级高于内置副本）。
  * 日志：exe 运行时写在 exe 同目录 logs/ 下（无写权限时自动降级为仅控制台输出）。

说明：exe 中内置了 psutil（可选依赖），可得到更准确的内存与路径信息；
     若构建环境没有 psutil，脚本仍可打包，程序会自动回退到 tasklist/ps 命令行模式。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
CONFIG_SRC = os.path.join(PROJECT_ROOT, "configs", "rules.json")
DIST_DIR = os.path.join(PROJECT_ROOT, "dist")
BUILD_DIR = os.path.join(PROJECT_ROOT, "build")
APP_NAME = "process-cleaner"
ENTRY = os.path.join(SRC_DIR, "process_cleaner", "__main__.py")


def check_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit(
            "未找到 PyInstaller。请先安装：\n"
            "    pip install -e .[build]         # 或 pip install pyinstaller>=6.0\n"
        )


def build(clean: bool = False, console: bool = True) -> int:
    check_pyinstaller()

    if clean:
        for path in (DIST_DIR, BUILD_DIR):
            if os.path.isdir(path):
                shutil.rmtree(path)
                print(f"[build] 已清理 {path}")

    os.makedirs(DIST_DIR, exist_ok=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",                       # 单文件 exe：免配置、可直接双击
        "--console" if console else "--windowed",
        "--name", APP_NAME,
        "--paths", SRC_DIR,                # 保证 process_cleaner 包可被找到
        "--add-data", f"{CONFIG_SRC}{os.pathsep}configs",   # 内置默认规则配置
        "--hidden-import", "process_cleaner.scanner",
        "--hidden-import", "process_cleaner.rules",
        "--distpath", DIST_DIR,
        "--workpath", BUILD_DIR,
        "--specpath", BUILD_DIR,
        ENTRY,
    ]

    # psutil 是可选依赖：装了就打进去（PyInstaller 自带 hook），没装则跳过
    try:
        import psutil  # noqa: F401
    except ImportError:
        print("[build] 未检测到 psutil，将打包为命令行回退模式（tasklist/ps）")

    print("[build] 执行:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, check=False)

    if result.returncode != 0:
        print("[build] 打包失败")
        return result.returncode

    exe_path = os.path.join(DIST_DIR, f"{APP_NAME}.exe")
    # 复制一份可编辑的规则配置到 exe 同目录，方便用户改规则（优先级高于内置副本）
    external_cfg_dir = os.path.join(DIST_DIR, "configs")
    os.makedirs(external_cfg_dir, exist_ok=True)
    if os.path.isfile(CONFIG_SRC):
        shutil.copy2(CONFIG_SRC, os.path.join(external_cfg_dir, "rules.json"))

    if os.path.isfile(exe_path):
        size_mb = os.path.getsize(exe_path) / 1024 / 1024
        print(f"[build] 完成: {exe_path} ({size_mb:.1f} MB)")
        print(f"[build] 外部配置: {os.path.join(external_cfg_dir, 'rules.json')} 可直接编辑")
    else:
        print("[build] 未找到产物 exe，请检查上面的构建日志")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="打包 process-cleaner 为 Windows 单文件 exe")
    parser.add_argument("--clean", action="store_true", help="构建前清理 dist/ 与 build/")
    parser.add_argument("--windowed", action="store_true", help="无控制台窗口（不适用于本 CLI）")
    args = parser.parse_args()
    return build(clean=args.clean, console=not args.windowed)


if __name__ == "__main__":
    sys.exit(main())
