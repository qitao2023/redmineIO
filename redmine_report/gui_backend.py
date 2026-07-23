"""Flask 后端 — 为 pywebview 前端提供 REST API。

所有 API 端点返回 JSON: {"ok": bool, "data": ..., "error": str}
底层模块 (config, client, generator, writer, models) 100% 复用，不做任何修改。
"""

import json
import os
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
import win32clipboard
from flask import Flask, jsonify, request

from .build_time import BUILD_TIME
from .client import RedmineClient, RedmineClientError
from .config import ConfigError, load_config
from .generator import generate_report
from .models import CancelledError, DailyReport

# ── Flask 应用 ──────────────────────────────────────────

app = Flask(__name__)

# 前端 HTML 文件路径
_FRONTEND_PATH = Path(__file__).resolve().parent / "gui_frontend.html"

# 异步生成任务存储
import uuid as _uuid
_task_store: dict[str, dict] = {}
_task_lock = threading.Lock()


# ── 静态首页 ────────────────────────────────────────────

@app.route("/")
def index():
    """返回前端单页面。"""
    if _FRONTEND_PATH.exists():
        return _FRONTEND_PATH.read_text(encoding="utf-8"), 200, {
            "Content-Type": "text/html; charset=utf-8"
        }
    return "<h1>前端文件未找到</h1>", 404


# ── 工具函数 ────────────────────────────────────────────

def _ok(data=None):
    return jsonify({"ok": True, "data": data or {}})


def _err(msg: str, code: int = 400):
    return jsonify({"ok": False, "error": msg}), code


def _get_config_dict() -> dict:
    """读取配置文件，返回字典（不抛异常）。"""
    config_path = Path.home() / ".redmine_report" / "config.yaml"
    try:
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception:
        pass
    return {}


# ── 配置相关 API ────────────────────────────────────────

@app.route("/api/load-config", methods=["GET"])
def api_load_config():
    """加载当前保存的配置。"""
    data = _get_config_dict()
    project_names = data.get("project_names", {})

    # 构建项目列表（离线还原，无需联网）
    # 展示所有已知项目：勾选的 + 未勾选的
    projects = []
    project_ids = data.get("project_ids", []) or []

    # 收集所有已知项目 ID（来自 project_names 和 project_ids）
    all_ids = set()
    for pid in project_ids:
        all_ids.add(str(pid))
    for pid_str in project_names.keys():
        all_ids.add(pid_str)

    for pid_str in sorted(all_ids, key=lambda x: int(x)):
        pid = int(pid_str)
        name = project_names.get(pid_str, f"项目 #{pid}")
        projects.append({
            "id": pid,
            "name": name,
            "checked": pid in project_ids,
        })

    return _ok({
        "url": data.get("redmine_url", ""),
        "api_key": data.get("api_key", ""),
        "project_ids": project_ids,
        "projects": projects,
        "skip_review": data.get("skip_review", False),
        "review_strict": data.get("review_strict", True),
        "support_always_new": data.get("support_always_new", False),
        "report_with_numbers": data.get("report_with_numbers", True),
    })


@app.route("/api/save-config", methods=["POST"])
def api_save_config():
    """保存配置到用户目录。"""
    body = request.get_json(force=True)
    config_dir = Path.home() / ".redmine_report"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.yaml"

    # 构建项目名映射
    projects = body.get("projects", [])
    project_names = {}
    for p in projects:
        project_names[str(p["id"])] = p.get("name", "")

    data = {
        "redmine_url": body.get("url", "").strip(),
        "api_key": body.get("api_key", "").strip(),
        "timezone": "Asia/Shanghai",
        "output_dir": "./reports",
        "project_ids": body.get("project_ids", []),
        "project_names": project_names,
        "skip_review": body.get("skip_review", False),
        "review_strict": body.get("review_strict", True),
        "support_always_new": body.get("support_always_new", False),
        "report_with_numbers": body.get("report_with_numbers", True),
    }

    try:
        config_path.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
        return _ok({"message": "设置已保存"})
    except Exception as e:
        return _err(f"保存失败: {e}")


@app.route("/api/reset-config", methods=["POST"])
def api_reset_config():
    """清除配置文件。"""
    config_path = Path.home() / ".redmine_report" / "config.yaml"
    try:
        config_path.unlink(missing_ok=True)
        return _ok({"message": "设置已重置"})
    except Exception as e:
        return _err(f"重置失败: {e}")


# ── Redmine 连接 API ────────────────────────────────────

@app.route("/api/test-connection", methods=["POST"])
def api_test_connection():
    """测试 Redmine 连接：认证 + 项目数。"""
    body = request.get_json(force=True)
    url = body.get("url", "").strip()
    api_key = body.get("api_key", "").strip()

    if not url:
        return _err("请输入服务器地址")
    if not api_key:
        return _err("请输入 API Key")

    try:
        client = RedmineClient(url=url, api_key=api_key)
        user = client.authenticate()
        projects = client.list_projects()
        return _ok({
            "success": True,
            "message": (
                f"✓ 连接成功\n"
                f"用户: {user['name']} ({user['login']})\n"
                f"可访问 {len(projects)} 个项目\n"
                f"服务器: {url}"
            ),
            "user": user,
            "project_count": len(projects),
        })
    except RedmineClientError as e:
        return _ok({
            "success": False,
            "message": f"连接失败\n\n{url}\n\n{str(e)}",
            "user": None,
            "project_count": 0,
        })
    except Exception as e:
        return _ok({
            "success": False,
            "message": f"未知错误\n\n{url}\n\n{str(e)[:400]}",
            "user": None,
            "project_count": 0,
        })


@app.route("/api/fetch-projects", methods=["POST"])
def api_fetch_projects():
    """从 Redmine API 获取项目列表。"""
    body = request.get_json(force=True)
    url = body.get("url", "").strip()
    api_key = body.get("api_key", "").strip()

    if not url or not api_key:
        return _err("请先输入服务器地址和 API Key")

    try:
        client = RedmineClient(url=url, api_key=api_key)
        user = client.authenticate()
        all_projects = client.list_projects()
        all_projects.sort(key=lambda p: p.get("name", ""))
        return _ok({
            "projects": all_projects,
            "user": user,
        })
    except RedmineClientError as e:
        return _err(f"获取失败: {e}")
    except Exception as e:
        return _err(f"未知错误: {str(e)[:200]}")


# ── 日报生成 API ────────────────────────────────────────

def _do_generate(url: str, api_key: str, report_date: str, end_time: str,
                 project_ids: list[int] | None, custom_other: str,
                 skip_review: bool, review_strict: bool,
                 support_always_new: bool, report_with_numbers: bool,
                 show_timing: bool,
                 progress_callback=None,
                 cancel_check=None) -> dict:
    """在后台线程中执行日报生成。"""
    client = RedmineClient(url=url, api_key=api_key)

    proj_ids = project_ids if project_ids else None
    report = client.build_report_data(
        report_date, project_ids=proj_ids,
        skip_review=skip_review, review_strict=review_strict,
        support_always_new=support_always_new,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    )

    content = generate_report(
        report, custom_other=custom_other,
        end_time=end_time.strip() or None,
        report_with_numbers=report_with_numbers,
    )

    timing = getattr(report, "timing", {})

    # 耗时分析：追加调试信息到日报内容
    if show_timing and timing:
        lines = []
        lines.append("")
        lines.append("──────── 耗时统计 ────────")
        lines.append(f"认证:          {timing.get('auth', '?')}s")
        lines.append(
            f"filter查询:    {timing.get('filter', '?')}s  "
            f"({timing.get('filter_queries', '?')}次请求, "
            f"{timing.get('filter_candidates', '?')}个候选)"
        )
        lines.append(
            f"预分类:        {timing.get('preclassify', '?')}s  "
            f"(新增{timing.get('preclassify_new', '?')} "
            f"+ 待验证{timing.get('preclassify_pending', '?')})"
        )
        search_err = timing.get('search_error', '')
        lines.append(
            f"搜索API预筛:   {timing.get('search_api', '?')}s  "
            f"(命中{timing.get('search_hit', '?')}个, "
            f"筛掉{timing.get('search_skipped', '?')}个无关)"
            f"{'  FAIL: ' + search_err if search_err else ''}"
        )
        lines.append(
            f"journal验证:   {timing.get('journal', '?')}s  "
            f"(检查{timing.get('journal_checked', '?')}个, "
            f"确认{timing.get('journal_confirmed', '?')}个)"
        )
        lines.append("─────────────────────────")
        lines.append(f"总耗时:        {timing.get('build_total', timing.get('total', '?'))}s")

        # 候选 Issue 详情
        candidates = timing.get("candidates_detail", [])
        if candidates:
            lines.append("")
            lines.append(f"──────── filter候选 Issue（{len(candidates)}个）────────")
            for line in candidates[:200]:
                lines.append(line)

        # 搜索 API 调试
        search_debug = timing.get("search_debug", "")
        if search_debug:
            lines.append("")
            lines.append("──────── 搜索API原始返回 ────────")
            for line in search_debug.split("\n")[:20]:
                lines.append(line)

        # review_strict 过滤日志
        rejected = timing.get("journal_rejected", [])
        if rejected:
            lines.append("")
            lines.append(f"──────── review_strict 过滤（{len(rejected)}个）────────")
            for line in rejected[:50]:
                lines.append(line)

        # journal 原始内容调试
        journal_debug = timing.get("journal_debug", [])
        if journal_debug:
            lines.append("")
            lines.append("──────── 已确认Issue的journal内容 ────────")
            for line in journal_debug[:60]:
                lines.append(line)

        content += "\n".join(lines) + "\n"

    summary = {
        "user_name": report.user_name,
        "date": report.date,
        "total_issues": report.total_issues,
        "project_count": report.project_count,
        "timing": timing,
    }

    return {
        "content": content,
        "summary": summary,
    }


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """生成日报。"""
    body = request.get_json(force=True)
    return _handle_generate(body)


@app.route("/api/analyze-performance", methods=["POST"])
def api_analyze_performance():
    """耗时分析：同 generate 但附带各阶段耗时。"""
    body = request.get_json(force=True)
    body["show_timing"] = True
    return _handle_generate(body)


def _handle_generate(body: dict):
    """处理日报生成请求（generate 和 analyze 共用）。
    改为异步：立即返回 task_id，前端轮询 /api/progress 获取结果。
    """
    url = body.get("url", "").strip()
    api_key = body.get("api_key", "").strip()
    report_date = body.get("date", "").strip()
    end_time = body.get("time", "").strip()
    project_ids = body.get("project_ids", []) or []
    custom_other = body.get("custom_other", "").strip()
    skip_review = body.get("skip_review", False)
    review_strict = body.get("review_strict", True)
    support_always_new = body.get("support_always_new", False)
    report_with_numbers = body.get("report_with_numbers", False)
    show_timing = body.get("show_timing", False)

    if not url or not api_key:
        return _err("请先输入服务器地址和 API Key")
    if not report_date:
        return _err("请输入报告日期")

    task_id = str(_uuid.uuid4())[:8]
    cancel_event = threading.Event()
    with _task_lock:
        _task_store[task_id] = {"status": "running", "progress": "准备中...",
                                 "pct": 0, "result": None, "error": None,
                                 "show_timing": show_timing,
                                 "cancel_event": cancel_event}

    def _run():
        try:
            def _on_progress(msg, pct):
                with _task_lock:
                    if task_id in _task_store:
                        _task_store[task_id]["progress"] = msg
                        _task_store[task_id]["pct"] = pct

            def _should_cancel():
                return cancel_event.is_set()

            result = _do_generate(
                url, api_key, report_date, end_time,
                project_ids, custom_other,
                skip_review, review_strict, support_always_new,
                report_with_numbers, show_timing,
                progress_callback=_on_progress,
                cancel_check=_should_cancel,
            )
            with _task_lock:
                if task_id in _task_store:
                    _task_store[task_id]["status"] = "done"
                    _task_store[task_id]["result"] = result
                    _task_store[task_id]["pct"] = 100
        except CancelledError:
            with _task_lock:
                if task_id in _task_store:
                    _task_store[task_id]["status"] = "cancelled"
                    _task_store[task_id]["progress"] = "已取消"
                    _task_store[task_id]["pct"] = 0
        except RedmineClientError as e:
            with _task_lock:
                if task_id in _task_store:
                    _task_store[task_id]["status"] = "error"
                    _task_store[task_id]["error"] = str(e)
        except Exception as e:
            with _task_lock:
                if task_id in _task_store:
                    _task_store[task_id]["status"] = "error"
                    _task_store[task_id]["error"] = f"未知错误: {str(e)[:300]}"

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return _ok({"task_id": task_id})


@app.route("/api/progress/<task_id>", methods=["GET"])
def api_progress(task_id):
    """查询异步生成任务的进度和结果。"""
    with _task_lock:
        task = _task_store.get(task_id)
    if task is None:
        return _err("任务不存在或已过期")
    return _ok({
        "status": task["status"],
        "progress": task["progress"],
        "pct": task["pct"],
        "result": task.get("result"),
        "error": task.get("error"),
        "show_timing": task.get("show_timing", False),
    })


@app.route("/api/cancel/<task_id>", methods=["POST"])
def api_cancel(task_id):
    """取消正在执行的异步生成任务。"""
    with _task_lock:
        task = _task_store.get(task_id)
    if task is None:
        return _err("任务不存在或已过期")
    if task["status"] != "running":
        return _ok({"message": "任务不在运行中，无需取消"})
    cancel_event = task.get("cancel_event")
    if cancel_event is not None:
        cancel_event.set()
    task["progress"] = "正在取消..."
    return _ok({"message": "已发送取消请求，正在终止..."})


# 定期清理过期任务
def _cleanup_old_tasks():
    """删除超过 10 分钟的旧任务。"""
    import time as _time
    while True:
        _time.sleep(300)
        with _task_lock:
            # 简单策略：保留最近 50 个
            while len(_task_store) > 50:
                oldest = next(iter(_task_store))
                del _task_store[oldest]

_cleanup_thread = threading.Thread(target=_cleanup_old_tasks, daemon=True)
_cleanup_thread.start()


# ── 剪贴板 API ──────────────────────────────────────────

@app.route("/api/copy", methods=["POST"])
def api_copy():
    """复制日报内容到 Windows 剪贴板（带 HTML 格式）。"""
    body = request.get_json(force=True)
    content = body.get("content", "")

    if not content:
        return _err("没有可复制的内容")

    html = _report_to_html(content)

    try:
        _write_clipboard_html(content, html)
        return _ok({"message": "日报已复制到剪贴板 ✓"})
    except Exception as e:
        # 回退：通过返回内容让前端用 navigator.clipboard 复制
        return _err(f"剪贴板复制失败: {e}")


# ── 数据诊断 API ────────────────────────────────────────

@app.route("/api/diagnose", methods=["POST"])
def api_diagnose():
    """数据诊断：查询当天 Issues，逐条打印原始数据。"""
    body = request.get_json(force=True)
    url = body.get("url", "").strip()
    api_key = body.get("api_key", "").strip()
    report_date = body.get("date", "").strip()

    if not url or not api_key:
        return _err("请先输入服务器地址和 API Key")

    try:
        from redminelib import Redmine

        rm = Redmine(url.rstrip("/"), key=api_key, requests={"timeout": 30})
        lines = []
        lines.append("# Redmine Issue 诊断报告\n")
        lines.append(f"**服务器**: {url}")
        lines.append(f"**目标日期**: {report_date}\n")

        # 用户
        lines.append("## [1] 当前用户\n")
        try:
            user = rm.user.get("current")
            lines.append(f"- ID: **{user.id}**")
            lines.append(
                f"- Login: **{getattr(user, 'login', '?')}**"
            )
            lines.append(
                f"- Name: **{getattr(user, 'lastname', '')}"
                f"{getattr(user, 'firstname', '')}**"
            )
            user_id = user.id
        except Exception as e:
            lines.append(f"获取用户失败: {e}\n")
            return _ok({"content": "\n".join(lines)})

        # 查询 Issues
        lines.append(f"## [2] 查询 Issues（updated_on={report_date}）\n")
        seen = set()
        all_issues = []

        lines.append(f"### 策略1: author_id={user_id}\n")
        try:
            authored = list(rm.issue.filter(
                author_id=user_id, updated_on=report_date,
                sort="updated_on:desc", limit=300,
            ))
            lines.append(f"结果: **{len(authored)}** 个\n")
            for iss in authored:
                if iss.id not in seen:
                    seen.add(iss.id)
                    all_issues.append(("author", iss))
        except Exception as e:
            lines.append(f"查询失败: {e}\n")

        lines.append(f"### 策略2: assigned_to_id={user_id}\n")
        try:
            assigned = list(rm.issue.filter(
                assigned_to_id=user_id, updated_on=report_date,
                sort="updated_on:desc", limit=300,
            ))
            lines.append(f"结果: **{len(assigned)}** 个\n")
            for iss in assigned:
                if iss.id not in seen:
                    seen.add(iss.id)
                    all_issues.append(("assigned", iss))
        except Exception as e:
            lines.append(f"查询失败: {e}\n")

        lines.append(f"**合并去重后: {len(all_issues)} 个 Issue**\n")

        if not all_issues:
            # 查最近几天
            lines.append("## [2b] 扫描最近 7 天\n")
            found_any = False
            for offset in range(1, 8):
                d = (date.today() - timedelta(days=offset)).isoformat()
                try:
                    cnt_a = len(list(rm.issue.filter(
                        author_id=user_id, updated_on=d, limit=300
                    )))
                    cnt_as = len(list(rm.issue.filter(
                        assigned_to_id=user_id, updated_on=d, limit=300
                    )))
                    if cnt_a + cnt_as > 0:
                        lines.append(
                            f"- **{d}**: author={cnt_a}, assigned={cnt_as}"
                        )
                        found_any = True
                except Exception:
                    pass
            if not found_any:
                lines.append("- 最近7天都没有 Issues！\n")
            return _ok({"content": "\n".join(lines)})

        return _ok({"content": "\n".join(lines)})

    except Exception as e:
        return _err(f"诊断失败: {str(e)[:400]}")


# ── 版本信息 API ────────────────────────────────────────

@app.route("/api/version", methods=["GET"])
def api_version():
    """返回构建时间。"""
    return _ok({"build_time": BUILD_TIME})


# ── 剪贴板辅助函数（从原 gui.py 移植）───────────────────

SECTION_COLORS = {
    "1）": "#3b82f6",
    "2）": "#10b981",
    "3）": "#ef4444",
    "4）": "#a855f7",
}


def _report_to_html(content: str) -> str:
    """将纯文本日报转为带颜色的 HTML。"""
    lines = content.split("\n")
    html_lines = []
    current_color = None

    for line in lines:
        escaped = (
            line.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

        color = None
        for prefix, c in SECTION_COLORS.items():
            if escaped.startswith(prefix):
                color = c
                current_color = c
                break

        if color is None and escaped.strip() and current_color:
            color = current_color

        if color:
            html_lines.append(
                f'<span style="color:{color}">{escaped}</span>'
            )
        else:
            html_lines.append(escaped)

    body = "\n".join(html_lines)
    return (
        f'<pre style="font-family:Consolas,monospace;font-size:12pt;'
        f'line-height:1.6;margin:0">{body}</pre>'
    )


def _write_clipboard_html(plain_text: str, html: str):
    """使用 Windows Clipboard API 同时写入 HTML 和纯文本。"""
    html_doc = (
        "<html><body>\r\n"
        "<!--StartFragment-->\r\n"
        f"{html}\r\n"
        "<!--EndFragment-->\r\n"
        "</body></html>"
    )

    pre_b = (
        "Version:0.9\r\n"
        "StartHTML:0000000000\r\n"
        "EndHTML:0000000000\r\n"
        "StartFragment:0000000000\r\n"
        "EndFragment:0000000000\r\n"
    ).encode("utf-8")
    html_b = html.encode("utf-8")
    doc_b = html_doc.encode("utf-8")
    frag_start_b = b"<html><body>\r\n<!--StartFragment-->\r\n"

    start_html = len(pre_b)
    end_html = len(pre_b) + len(doc_b)
    start_frag = len(pre_b) + len(frag_start_b)
    end_frag = start_frag + len(html_b)

    clipboard_html = (
        f"Version:0.9\r\n"
        f"StartHTML:{start_html:09d}\r\n"
        f"EndHTML:{end_html:09d}\r\n"
        f"StartFragment:{start_frag:09d}\r\n"
        f"EndFragment:{end_frag:09d}\r\n"
        f"{html_doc}"
    )

    try:
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        CF_HTML = win32clipboard.RegisterClipboardFormat("HTML Format")
        win32clipboard.SetClipboardData(CF_HTML, clipboard_html)
        win32clipboard.SetClipboardData(
            win32clipboard.CF_UNICODETEXT, plain_text
        )
    finally:
        win32clipboard.CloseClipboard()
