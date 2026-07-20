#!/usr/bin/env python
"""Redmine 日报生成工具 — GUI 启动入口。

双击此文件或运行:
    python run_gui.py
"""

import sys
from pathlib import Path

# 确保项目根目录在 sys.path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from redmine_report.gui_webview import run_gui

if __name__ == "__main__":
    run_gui()
