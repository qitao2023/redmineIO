# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 — 生成独立 .exe 文件。

构建命令:
    pyinstaller redmine_report.spec

或在 Windows 上双击 build.bat
"""

import sys
from pathlib import Path

# 项目根目录
ROOT = Path(SPECPATH)  # SPECPATH 是 .spec 文件所在目录

# ── 需要收集的隐藏导入 ──
hidden_imports = [
    # Flask / Werkzeug
    "flask",
    "flask.json",
    "werkzeug",
    "werkzeug.serving",
    # pywebview
    "webview",
    "webview.platforms",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "webview.js",
    # redminelib
    "redminelib",
    "redminelib.resources",
    "redminelib.managers",
    "redminelib.exceptions",
    # PyYAML
    "yaml",
    # win32clipboard
    "win32clipboard",
    "pywin32",
    # 标准库可能被遗漏的
    "json",
    "datetime",
    "pathlib",
    "threading",
    "concurrent.futures",
]

# ── 需要收集的数据文件 ──
datas = [
    # 前端 HTML 文件
    (
        str(ROOT / "redmine_report" / "gui_frontend.html"),
        "redmine_report",
    ),
]

# ── Spec 配置 ──
a = Analysis(
    [str(ROOT / "run_gui.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不需要的大型库，减小 exe 体积
        "tkinter",
        "tkinter.test",
        "customtkinter",
        "unittest",
        "pydoc",
        "setuptools",
        "pip",
        "PIL",
        "numpy",
        "pandas",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# GUI 模式：console=False 不显示命令行窗口
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Redmine日报工具",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI 模式：不显示终端
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "icon.ico") if (ROOT / "icon.ico").exists() else None,
)
