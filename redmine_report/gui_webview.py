"""pywebview GUI — 使用现代 Web 前端替代 customtkinter。

架构：
  - Flask 在后台线程中运行，提供 REST API
  - pywebview 嵌入系统 WebView (Edge WebView2 on Windows)
  - 前端是单个 HTML 文件，内联 CSS/JS
"""

import logging
import socket
import sys
import threading
from pathlib import Path

import webview

from .build_time import BUILD_TIME
from .gui_backend import app

# ── 端口探测 ──────────────────────────────────────────────

def _find_free_port() -> int:
    """找到一个可用的 TCP 端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── Flask 启动 ───────────────────────────────────────────

def _start_flask(port: int):
    """在 daemon 线程中启动 Flask。"""
    # 抑制 Flask/Werkzeug 的日志输出
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)

    app.run(
        host="127.0.0.1",
        port=port,
        debug=False,
        use_reloader=False,
    )


# ── pywebview 入口 ────────────────────────────────────────

def run_gui():
    """启动 GUI 应用：Flask 后端 + pywebview 窗口。"""
    port = _find_free_port()
    url = f"http://127.0.0.1:{port}"

    # 启动 Flask 后台线程
    flask_thread = threading.Thread(
        target=_start_flask, args=(port,), daemon=True
    )
    flask_thread.start()

    # 窗口标题
    title = f"Redmine 日报生成工具 [{BUILD_TIME}]" if BUILD_TIME else "Redmine 日报生成工具"

    # 尝试加载图标
    icon_path = None
    for candidate in [
        Path(__file__).resolve().parent.parent / "icon.ico",
        Path(sys._MEIPASS) / "icon.ico" if getattr(sys, "frozen", False) else None,
    ]:
        if candidate and candidate.exists():
            icon_path = str(candidate)
            break

    # 创建 pywebview 窗口
    window = webview.create_window(
        title=title,
        url=url,
        width=1100,
        height=740,
        min_size=(900, 550),
        resizable=True,
        fullscreen=False,
        easy_drag=True,
    )

    # 启动事件循环（阻塞直到窗口关闭）
    webview.start(debug=False)


# ── 直接运行支持 ─────────────────────────────────────────

if __name__ == "__main__":
    run_gui()
