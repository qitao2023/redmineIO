"""CustomTkinter GUI — 图形界面版日报生成工具。

与 CLI 平级，100% 复用底层模块 (config, client, generator, writer)。
"""

import os
import sys
import threading
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .config import ConfigError, load_config
from .client import RedmineClient, RedmineClientError
from .generator import generate_report
from .writer import write_report

# ── 常量 ──────────────────────────────────────────────
APP_TITLE = "Redmine 日报生成工具"
WINDOW_WIDTH = 1100
WINDOW_HEIGHT = 700
LEFT_PANEL_WIDTH = 320
PAD_X = 12
PAD_Y = 8

# ── 主应用类 ──────────────────────────────────────────


class RedmineReportApp(ctk.CTk):
    """Redmine 日报生成工具主窗口。"""

    def __init__(self):
        super().__init__()

        # 窗口设置
        self.title(APP_TITLE)
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.minsize(900, 550)

        # 主题
        ctk.set_appearance_mode("system")  # 跟随系统
        ctk.set_default_color_theme("blue")

        # 状态
        self._report_content: str | None = None
        self._report_date: str = ""
        self._client: RedmineClient | None = None

        # 构建界面
        self._build_layout()

        # 启动时自动尝试加载配置
        self.after(200, self._auto_load_config)

    # ── 布局构建 ────────────────────────────────────

    def _build_layout(self):
        """构建主布局：两栏 + 底部状态栏。"""
        # 主容器使用 grid
        self.grid_columnconfigure(0, weight=0, minsize=LEFT_PANEL_WIDTH)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        # 左侧设置面板
        self._create_settings_panel()

        # 右侧预览面板
        self._create_preview_panel()

        # 底部状态栏
        self._create_status_bar()

    def _create_settings_panel(self):
        """左侧设置面板。"""
        frame = ctk.CTkFrame(self, corner_radius=10)
        frame.grid(row=0, column=0, padx=(PAD_X, PAD_X // 2), pady=PAD_Y, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)

        row = 0

        # ── 标题 ──
        ctk.CTkLabel(
            frame, text="⚙ 连接设置",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=row, column=0, padx=PAD_X, pady=(PAD_Y + 4, PAD_Y), sticky="w")
        row += 1

        # ── 服务器地址 ──
        ctk.CTkLabel(frame, text="服务器地址", anchor="w").grid(
            row=row, column=0, padx=PAD_X, pady=(PAD_Y, 0), sticky="w"
        )
        row += 1
        self.url_entry = ctk.CTkEntry(
            frame,
            placeholder_text="http://192.168.0.83:8181/redmine/",
            height=34,
        )
        self.url_entry.grid(row=row, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="ew")
        row += 1

        # ── API Key ──
        key_label_frame = ctk.CTkFrame(frame, fg_color="transparent")
        key_label_frame.grid(row=row, column=0, padx=PAD_X, pady=(PAD_Y, 0), sticky="ew")
        ctk.CTkLabel(key_label_frame, text="API Key", anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        self.key_help_btn = ctk.CTkButton(
            key_label_frame,
            text="?",
            width=22,
            height=22,
            corner_radius=11,
            fg_color=("gray65", "gray50"),
            hover_color=("gray55", "gray40"),
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._show_key_help,
        )
        self.key_help_btn.grid(row=0, column=1, padx=(4, 0), sticky="w")
        row += 1

        key_frame = ctk.CTkFrame(frame, fg_color="transparent")
        key_frame.grid(row=row, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="ew")
        key_frame.grid_columnconfigure(0, weight=1)
        key_frame.grid_columnconfigure(1, weight=0)

        self.key_entry = ctk.CTkEntry(
            key_frame,
            placeholder_text="输入 API 访问密钥",
            show="•",
            height=34,
        )
        self.key_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self._key_visible = False
        self.eye_btn = ctk.CTkButton(
            key_frame,
            text="👁",
            width=36,
            height=34,
            command=self._toggle_key_visibility,
        )
        self.eye_btn.grid(row=0, column=1)
        row += 1

        # ── 报告日期 ──
        ctk.CTkLabel(frame, text="报告日期", anchor="w").grid(
            row=row, column=0, padx=PAD_X, pady=(PAD_Y, 0), sticky="w"
        )
        row += 1

        date_frame = ctk.CTkFrame(frame, fg_color="transparent")
        date_frame.grid(row=row, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="ew")
        date_frame.grid_columnconfigure(0, weight=1)
        date_frame.grid_columnconfigure(1, weight=0)

        today_str = date.today().isoformat()
        self.date_entry = ctk.CTkEntry(
            date_frame,
            placeholder_text="YYYY-MM-DD",
            height=34,
        )
        self.date_entry.insert(0, today_str)
        self.date_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.today_btn = ctk.CTkButton(
            date_frame,
            text="今天",
            width=50,
            height=34,
            command=self._set_today,
        )
        self.today_btn.grid(row=0, column=1)
        row += 1

        # ── 分隔线 ──
        ctk.CTkFrame(frame, height=2, fg_color=("gray70", "gray30")).grid(
            row=row, column=0, padx=PAD_X, pady=(PAD_Y, PAD_Y), sticky="ew"
        )
        row += 1

        # ── 测试连接 + 加载配置 ──
        btn_row = ctk.CTkFrame(frame, fg_color="transparent")
        btn_row.grid(row=row, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="ew")
        btn_row.grid_columnconfigure(0, weight=1)
        btn_row.grid_columnconfigure(1, weight=1)

        self.test_btn = ctk.CTkButton(
            btn_row,
            text="🔍 测试连接",
            command=self._test_connection,
            height=34,
            fg_color="#6b7280",
            hover_color="#4b5563",
        )
        self.test_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))

        self.save_cfg_btn = ctk.CTkButton(
            btn_row,
            text="💾 保存Key",
            command=self._save_config,
            height=34,
        )
        self.save_cfg_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        row += 1

        # ── 生成按钮 ──
        self.generate_btn = ctk.CTkButton(
            frame,
            text="🚀 生成日报",
            command=self._generate_report,
            height=40,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.generate_btn.grid(row=row, column=0, padx=PAD_X, pady=(PAD_Y, PAD_Y), sticky="ew")
        row += 1

        # ── 保存按钮 ──
        self.copy_btn = ctk.CTkButton(
            frame,
            text="📋 复制日报",
            command=self._copy_report,
            height=34,
            state="disabled",
        )
        self.copy_btn.grid(row=row, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="ew")
        row += 1

        # ── 数据诊断按钮 ──
        self.diag_btn = ctk.CTkButton(
            frame,
            text="🔬 数据诊断",
            command=self._diagnose_data,
            height=30,
            fg_color="transparent",
            border_width=1,
            border_color=("gray50", "gray40"),
            text_color=("gray50", "gray60"),
            font=ctk.CTkFont(size=11),
        )
        self.diag_btn.grid(row=row, column=0, padx=PAD_X, pady=(0, PAD_Y // 2), sticky="ew")
        row += 1

        # ── 其他补充输入 ──
        ctk.CTkLabel(frame, text="其他补充（自定义内容）", anchor="w",
                     font=ctk.CTkFont(size=11)).grid(
            row=row, column=0, padx=PAD_X, pady=(PAD_Y, 0), sticky="w"
        )
        row += 1
        self.other_text = ctk.CTkTextbox(frame, height=100, wrap="word",
                                          font=ctk.CTkFont(size=12))
        self.other_text.grid(row=row, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="ew")
        row += 1

        # ── 摘要信息 ──
        self.summary_label = ctk.CTkLabel(
            frame,
            text="",
            anchor="w",
            justify="left",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        )
        self.summary_label.grid(row=row, column=0, padx=PAD_X, pady=(PAD_Y, PAD_Y), sticky="ew")

    def _create_preview_panel(self):
        """右侧日报预览区。"""
        frame = ctk.CTkFrame(self, corner_radius=10)
        frame.grid(row=0, column=1, padx=(PAD_X // 2, PAD_X), pady=PAD_Y, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=0)
        frame.grid_rowconfigure(1, weight=1)

        # 标题
        ctk.CTkLabel(
            frame,
            text="📋 日报预览",
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, padx=PAD_X, pady=(PAD_Y, 4), sticky="w")

        # 文本区域
        self.preview_text = ctk.CTkTextbox(
            frame,
            wrap="none",
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            activate_scrollbars=True,
        )
        self.preview_text.grid(row=1, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="nsew")

        # 手动添加水平滚动条
        self.h_scroll = ctk.CTkScrollbar(frame, orientation="horizontal",
                                          command=self.preview_text._textbox.xview)
        self.h_scroll.grid(row=2, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="ew")
        self.preview_text._textbox.configure(xscrollcommand=self.h_scroll.set)

        self.preview_text.insert("1.0", "请填写左侧连接信息，然后点击「生成日报」。\n\n生成的日报内容将在此处显示。")
        self.preview_text.configure(state="disabled")  # 只读

    def _create_status_bar(self):
        """底部状态栏。"""
        frame = ctk.CTkFrame(self, corner_radius=0, height=32)
        frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_columnconfigure(1, weight=0)

        self.status_label = ctk.CTkLabel(
            frame,
            text="就绪",
            anchor="w",
            font=ctk.CTkFont(size=11),
        )
        self.status_label.grid(row=0, column=0, padx=PAD_X, pady=(2, 2), sticky="w")

        self.progress = ctk.CTkProgressBar(frame, width=120, height=10)
        self.progress.grid(row=0, column=1, padx=PAD_X, pady=(2, 2))
        self.progress.set(0)
        self.progress.grid_remove()  # 默认隐藏

    # ── 交互逻辑 ────────────────────────────────────

    def _show_key_help(self):
        """显示 API Key 获取帮助。"""
        messagebox.showinfo(
            "如何获取 API Key",
            "1. 打开浏览器登录 Redmine\n"
            "2. 点击右上角「我的账号」\n"
            "3. 右侧边栏找到「API 访问键」\n"
            "4. 点击「显示」复制密钥\n\n"
            f"服务器地址: {self.url_entry.get() or '(未填写)'}\n"
            f"可在地址后追加 /my/account 直达",
        )

    def _toggle_key_visibility(self):
        """切换 API Key 显示/隐藏。"""
        self._key_visible = not self._key_visible
        if self._key_visible:
            self.key_entry.configure(show="")
            self.eye_btn.configure(text="🙈")
        else:
            self.key_entry.configure(show="•")
            self.eye_btn.configure(text="👁")

    def _set_today(self):
        """日期设为今天。"""
        self.date_entry.delete(0, "end")
        self.date_entry.insert(0, date.today().isoformat())

    def _validate_date(self, date_str: str) -> bool:
        """验证日期格式 YYYY-MM-DD。"""
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    DEFAULT_URL = "http://192.168.0.83:8181/redmine/"

    def _auto_load_config(self):
        """启动时自动尝试加载配置。"""
        # 先设默认值
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, self.DEFAULT_URL)

        try:
            cfg = load_config()
            if cfg.redmine_url:
                self.url_entry.delete(0, "end")
                self.url_entry.insert(0, cfg.redmine_url)
            if cfg.api_key:
                self.key_entry.delete(0, "end")
                self.key_entry.insert(0, cfg.api_key)
        except ConfigError:
            pass  # 没有配置文件也正常

    def _load_config_file(self):
        """手动加载配置文件。"""
        filepath = filedialog.askopenfilename(
            title="选择配置文件",
            filetypes=[
                ("YAML 文件", "*.yaml *.yml"),
                ("所有文件", "*.*"),
            ],
            initialfile="config.yaml",
        )
        if not filepath:
            return

        try:
            cfg = load_config(config_path=filepath)
            if cfg.redmine_url:
                self.url_entry.delete(0, "end")
                self.url_entry.insert(0, cfg.redmine_url)
            if cfg.api_key:
                self.key_entry.delete(0, "end")
                self.key_entry.insert(0, cfg.api_key)
            self._set_status("配置已加载")
        except ConfigError as e:
            messagebox.showerror("配置错误", str(e))

    def _save_config(self):
        """将当前 URL 和 Key 保存到 exe 同目录的 config.yaml。"""
        try:
            import sys
            import yaml

            # 保存到 exe 所在目录（打包后）或当前目录（源码运行）
            if getattr(sys, "frozen", False):
                config_path = Path(sys.executable).parent / "config.yaml"
            else:
                config_path = Path("config.yaml")

            data = {
                "redmine_url": self.url_entry.get().strip(),
                "api_key": self.key_entry.get().strip(),
                "timezone": "Asia/Shanghai",
                "output_dir": "./reports",
            }
            config_path.write_text(
                yaml.dump(data, allow_unicode=True, default_flow_style=False),
                encoding="utf-8",
            )
            self._set_status(f"Key 已保存到 {config_path}")
        except Exception:
            pass  # 保存失败不影响主流程

    def _test_connection(self):
        """测试 Redmine 连接：验证 URL + API Key。"""
        url = self.url_entry.get().strip()
        api_key = self.key_entry.get().strip()

        if not url:
            messagebox.showwarning("输入错误", "请先输入服务器地址。")
            self.url_entry.focus_set()
            return
        if not api_key:
            messagebox.showwarning("输入错误", "请先输入 API Key。")
            self.key_entry.focus_set()
            return

        # 进入加载状态
        self.test_btn.configure(state="disabled", text="⏳ 测试中...")
        self._set_status("正在测试连接...")
        self._show_progress(True)

        def do_test():
            result_parts = []
            ok = True
            try:
                # Step 1: 初始化
                client = RedmineClient(url=url, api_key=api_key)
                result_parts.append("✓ 连接对象创建成功")

                # Step 2: 认证
                user = client.authenticate()
                result_parts.append(
                    f"✓ 认证成功 — 用户: {user['name']} ({user['login']})"
                )

                # Step 3: 项目数
                projects = client.list_projects()
                result_parts.append(f"✓ 可访问 {len(projects)} 个项目")

                # 结果汇总
                msg = "\n".join(result_parts)
                msg += f"\n\n服务器: {url}\n用户ID: {user['id']}\n项目数: {len(projects)}"
                self.after(0, lambda: self._on_test_success(msg))

            except RedmineClientError as e:
                self.after(0, lambda: self._on_test_error(
                    f"连接失败\n\n{url}\n\n{str(e)}"
                ))
            except Exception as e:
                self.after(0, lambda: self._on_test_error(
                    f"未知错误\n\n{url}\n\n{str(e)[:400]}"
                ))

        threading.Thread(target=do_test, daemon=True).start()

    def _on_test_success(self, msg: str):
        self._save_config()  # 自动保存 Key，下次不用再输
        self.test_btn.configure(state="normal", text="🔍 测试连接")
        self._show_progress(False)
        self._set_status("连接测试通过 ✓ — Key 已保存")
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert(
            "1.0",
            f"# 🔍 连接测试结果\n\n连接成功！以下是你 Redmine 账号的信息：\n\n"
            f"```\n{msg}\n```\n\n"
            f"> 接下来请点击 **🚀 生成日报** 获取工作内容。\n"
            f"> Key 已自动保存到 config.yaml，下次打开无需重新输入。"
        )
        self.preview_text.configure(state="disabled")
        messagebox.showinfo("连接成功", msg)

    def _on_test_error(self, msg: str):
        self.test_btn.configure(state="normal", text="🔍 测试连接")
        self._show_progress(False)
        self._set_status("连接测试失败 ✗")
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert(
            "1.0",
            f"# 🔍 连接测试失败\n\n```\n{msg}\n```\n\n"
            f"### 常见原因：\n"
            f"1. **服务器地址错误** — 确认 URL 是否与浏览器中访问 Redmine 的地址一致\n"
            f"2. **API Key 错误** — Redmine → 我的账号 → API 访问键 → 显示 → 复制\n"
            f"3. **网络不通** — 确认本机能 ping 通服务器\n"
            f"4. **SSL/证书问题** — 内网可能使用自签名证书\n"
        )
        self.preview_text.configure(state="disabled")
        messagebox.showerror("连接失败", msg)

    def _generate_report(self):
        """后台线程生成日报。"""
        # 1. 读取输入
        url = self.url_entry.get().strip()
        api_key = self.key_entry.get().strip()
        report_date = self.date_entry.get().strip()

        # 2. 验证
        if not url:
            messagebox.showwarning("输入错误", "请输入 Redmine 服务器地址。")
            self.url_entry.focus_set()
            return
        if not api_key:
            messagebox.showwarning("输入错误", "请输入 API Key。")
            self.key_entry.focus_set()
            return
        if not self._validate_date(report_date):
            messagebox.showwarning("输入错误", f"日期格式错误: {report_date}\n请使用 YYYY-MM-DD 格式。")
            self.date_entry.focus_set()
            return

        # 3. UI 进入加载状态
        self.generate_btn.configure(state="disabled", text="⏳ 获取中...")
        self.copy_btn.configure(state="disabled")
        self._show_progress(True)
        self._set_status(f"正在连接 Redmine...")

        # 4. 后台线程执行
        thread = threading.Thread(
            target=self._do_generate,
            args=(url, api_key, report_date, self.other_text.get("1.0", "end").strip()),
            daemon=True,
        )
        thread.start()

    def _do_generate(self, url: str, api_key: str, report_date: str, custom_other: str = ""):
        """后台线程：实际执行 API 调用和日报生成。"""
        error_msg: str | None = None
        report = None
        content: str | None = None

        try:
            # 连接
            self._after_status("正在验证 API Key...")
            client = RedmineClient(url=url, api_key=api_key)
            self._client = client

            # 获取数据
            self._after_status(f"正在获取 {report_date} 的工作记录...")
            report = client.build_report_data(report_date)

            # 生成 Markdown
            self._after_status("正在生成日报...")
            content = generate_report(report, custom_other=custom_other)
            self._report_date = report_date

        except RedmineClientError as e:
            error_msg = str(e)
        except Exception as e:
            error_msg = f"未知错误: {str(e)[:300]}"

        # 回到主线程更新 UI
        if error_msg:
            self.after(0, lambda: self._on_generate_error(error_msg))
        else:
            self.after(0, lambda: self._on_generate_success(report, content))

    def _on_generate_success(self, report, content: str):
        """生成成功：更新预览。"""
        self._report_content = content

        # 更新预览区 + 分节着色
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", content)

        # 配置颜色标签
        section_colors = {
            "1、": "#3b82f6",  # 蓝 — 支持
            "2、": "#10b981",  # 绿 — 功能
            "3、": "#ef4444",  # 红 — BUG
            "4、": "#c026d3",  # 洋红 — 其他
        }
        for prefix, color in section_colors.items():
            self.preview_text.tag_config(prefix, foreground=color)
        self.preview_text.tag_config("title", foreground="#1e3a5f")

        # 逐行打标签 — 标题和条目同色，跨节自动切换
        current_section: str | None = None
        total_lines = int(self.preview_text.index("end-1c").split(".")[0])
        for line_num in range(1, total_lines + 1):
            start = f"{line_num}.0"
            end = f"{line_num}.end"
            line_text = self.preview_text.get(start, end)

            # 标题行（姓名-周X工作汇报）
            if "工作汇报" in line_text:
                self.preview_text.tag_add("title", start, end)
                continue

            # 节标题 → 切换当前节
            for prefix in section_colors:
                if line_text.startswith(prefix):
                    current_section = prefix
                    self.preview_text.tag_add(prefix, start, end)
                    break
            else:
                # 非标题行 → 用当前节的颜色
                if line_text.startswith("===") or line_text.startswith("考勤") or line_text.startswith("上班") or line_text.startswith("中途"):
                    pass  # 默认颜色（黑色）
                elif current_section and line_text.strip():
                    self.preview_text.tag_add(current_section, start, end)

        self.preview_text.configure(state="disabled")

        # 更新摘要
        summary = (
            f"用户: {report.user_name}\n"
            f"问题: {report.total_issues}个 | "
            f"项目: {report.project_count}个"
        )
        self.summary_label.configure(text=summary)

        # 恢复 UI
        self.generate_btn.configure(state="normal", text="🚀 生成日报")
        self.copy_btn.configure(state="normal")
        self._show_progress(False)
        self._set_status(f"日报已生成 — {report.date} | "
                         f"{report.total_issues}个问题")

    def _on_generate_error(self, error_msg: str):
        """生成失败：显示错误。"""
        self.generate_btn.configure(state="normal", text="🚀 生成日报")
        self._show_progress(False)
        self._set_status("生成失败")
        messagebox.showerror("生成失败", error_msg)

    def _copy_report(self):
        """复制日报到剪贴板。"""
        if not self._report_content:
            return
        self.clipboard_clear()
        self.clipboard_append(self._report_content)
        self._set_status("日报已复制到剪贴板 ✓")
        messagebox.showinfo("已复制", "日报内容已复制到剪贴板，可直接粘贴。")

    def _diagnose_data(self):
        """数据诊断：查询当天 Issues，逐条打印原始数据。"""
        url = self.url_entry.get().strip()
        api_key = self.key_entry.get().strip()
        report_date = self.date_entry.get().strip()

        if not url or not api_key:
            messagebox.showwarning("输入错误", "请先输入服务器地址和 API Key。")
            return

        self.diag_btn.configure(state="disabled", text="⏳ 诊断中...")
        self._set_status("正在查询 Issues...")
        self._show_progress(True)

        def _do():
            lines = []
            from datetime import date as dt_date, timedelta
            from redminelib import Redmine

            rm = Redmine(url.rstrip("/"), key=api_key, requests={"timeout": 30})
            lines.append("# Redmine Issue 诊断报告\n")
            lines.append(f"**服务器**: {url}")
            lines.append(f"**目标日期**: {report_date}\n")

            # 1. 用户
            lines.append("## [1] 当前用户\n")
            try:
                user = rm.user.get("current")
                lines.append(f"- ID: **{user.id}**")
                lines.append(f"- Login: **{getattr(user, 'login', '?')}**")
                lines.append(f"- Name: **{getattr(user, 'lastname', '')}{getattr(user, 'firstname', '')}**")
                user_id = user.id
            except Exception as e:
                lines.append(f"获取用户失败: {e}\n")
                self.after(0, lambda: self._show_diag_result("\n".join(lines)))
                return

            # 2. 查询策略1: author_id + updated_on
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
                    d = (dt_date.today() - timedelta(days=offset)).isoformat()
                    try:
                        cnt_a = len(list(rm.issue.filter(
                            author_id=user_id, updated_on=d, limit=300
                        )))
                        cnt_as = len(list(rm.issue.filter(
                            assigned_to_id=user_id, updated_on=d, limit=300
                        )))
                        if cnt_a + cnt_as > 0:
                            lines.append(f"- **{d}**: author={cnt_a}, assigned={cnt_as}")
                            found_any = True
                    except Exception:
                        pass
                if not found_any:
                    lines.append("- 最近7天都没有 Issues！\n")
                self.after(0, lambda: self._show_diag_result("\n".join(lines)))
                return

            # 3. 逐条详情
            lines.append("## [3] 逐条详情\n")
            for i, (source, iss) in enumerate(all_issues, 1):
                upd = getattr(iss, 'updated_on', '?')
                time_str = ""
                try:
                    s = str(upd)
                    if "T" in s:
                        time_str = s.split("T")[1][:5]
                    elif " " in s:
                        time_str = s.split(" ")[1][:5]
                except Exception:
                    pass

                lines.append(f"### [{i}] #{iss.id} [{source}]\n")
                lines.append("| 字段 | 值 |")
                lines.append("|------|-----|")
                lines.append(f"| id | {iss.id} |")
                lines.append(f"| subject | {iss.subject} |")
                lines.append(f"| tracker | {getattr(iss.tracker, 'name', '?')} |")
                lines.append(f"| status | {getattr(iss.status, 'name', '?')} |")
                lines.append(f"| priority | {getattr(iss.priority, 'name', '?')} |")
                lines.append(f"| project | {getattr(iss.project, 'name', '?')} |")
                lines.append(f"| updated_on | {upd} |")
                lines.append(f"| time(HH:MM) | {time_str} |")
                lines.append("")

            lines.append("---\n")
            lines.append("## [4] 汇总\n")
            lines.append(f"- 查询到 **{len(all_issues)}** 个 Issue\n")
            lines.append("> 如果上面 Issue 数据正常但日报为空，截图发给我。")

            self.after(0, lambda: self._show_diag_result("\n".join(lines)))

        threading.Thread(target=_do, daemon=True).start()

    def _show_diag_result(self, text: str):
        """在预览区显示诊断结果。"""
        self.diag_btn.configure(state="normal", text="🔬 数据诊断")
        self._show_progress(False)
        self._set_status("诊断完成 — 请查看右侧结果")
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", text)
        self.preview_text.configure(state="disabled")

    # ── UI 辅助方法 ──────────────────────────────────

    def _set_status(self, text: str):
        """更新状态栏文字。"""
        self.status_label.configure(text=text)

    def _after_status(self, text: str):
        """线程安全地更新状态栏。"""
        self.after(0, lambda: self._set_status(text))

    def _show_progress(self, show: bool):
        """显示/隐藏进度条。"""
        if show:
            self.progress.grid()
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.grid_remove()


# ── 入口 ──────────────────────────────────────────────


def run_gui():
    """启动 GUI 应用。"""
    app = RedmineReportApp()
    app.mainloop()


if __name__ == "__main__":
    run_gui()
