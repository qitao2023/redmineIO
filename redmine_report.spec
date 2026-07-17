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
    # customtkinter 内部模块
    "customtkinter",
    "customtkinter.windows",
    "customtkinter.windows.widgets",
    "customtkinter.windows.widgets.core_widget_classes",
    "customtkinter.windows.widgets.theme",
    "customtkinter.themes",
    # redminelib
    "redminelib",
    "redminelib.resources",
    "redminelib.managers",
    "redminelib.exceptions",
    # PyYAML
    "yaml",
    # 标准库可能被遗漏的
    "json",
    "datetime",
    "pathlib",
    "threading",
]

# ── 需要收集的数据文件 ──
datas = [
]

# 收集 customtkinter 的主题资源
try:
    import customtkinter as ctk
    ctk_dir = Path(ctk.__file__).parent
    # 主题 JSON 文件
    theme_dir = ctk_dir
    for pattern in ["*.json"]:
        for f in theme_dir.glob(pattern):
            datas.append((str(f), "customtkinter"))
except ImportError:
    pass

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
        "tkinter.test",
        "unittest",
        "pydoc",
        "distutils",
        "setuptools",
        "pip",
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
