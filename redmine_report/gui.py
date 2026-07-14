"""CustomTkinter GUI — 图形界面版日报生成工具。

与 CLI 平级，100% 复用底层模块 (config, client, generator, writer)。
"""

import os
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import win32clipboard

try:
    from .build_time import BUILD_TIME
except ImportError:
    BUILD_TIME = ""
from .config import ConfigError, load_config
from .client import RedmineClient, RedmineClientError
from .generator import generate_report
from .writer import write_report

# ── 常量 ──────────────────────────────────────────────
APP_TITLE = "Redmine 日报生成工具"
WINDOW_WIDTH = 1100
WINDOW_HEIGHT = 700
LEFT_PANEL_WIDTH = 400
PAD_X = 12
PAD_Y = 8

# ── 主应用类 ──────────────────────────────────────────


class RedmineReportApp(ctk.CTk):
    """Redmine 日报生成工具主窗口。"""

    def __init__(self):
        super().__init__()

        # 窗口设置
        build_tag = f"  [{BUILD_TIME}]" if BUILD_TIME else ""
        self.title(APP_TITLE + build_tag)
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
        """左侧设置面板 — 双 Tab 布局（手写切换，左对齐）。"""
        frame = ctk.CTkFrame(self, corner_radius=10, width=LEFT_PANEL_WIDTH)
        frame.grid(row=0, column=0, padx=(PAD_X, PAD_X // 2), pady=PAD_Y, sticky="nsew")
        frame.grid_propagate(False)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        # ── 自定义 Tab 按钮行（左对齐）──
        tab_btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        tab_btn_frame.grid(row=0, column=0, padx=4, pady=(4, 0), sticky="ew")

        self._tab_gen_btn = ctk.CTkButton(
            tab_btn_frame, text="日报生成", width=80, height=28,
            font=ctk.CTkFont(size=12),
            command=lambda: self._switch_tab("gen"),
        )
        self._tab_gen_btn.grid(row=0, column=0, padx=(0, 2), sticky="w")

        self._tab_cfg_btn = ctk.CTkButton(
            tab_btn_frame, text="设置", width=80, height=28,
            font=ctk.CTkFont(size=12),
            command=lambda: self._switch_tab("cfg"),
        )
        self._tab_cfg_btn.grid(row=0, column=1, sticky="w")

        # ── Tab 内容区 ──
        content_frame = ctk.CTkFrame(frame, fg_color="transparent")
        content_frame.grid(row=1, column=0, padx=4, pady=(0, 4), sticky="nsew")
        content_frame.grid_columnconfigure(0, weight=1)
        content_frame.grid_rowconfigure(0, weight=1)

        # 两个内容 frame 叠放在同一位置
        self._tab_gen = ctk.CTkFrame(content_frame, fg_color="transparent")
        self._tab_gen.grid(row=0, column=0, sticky="nsew")
        self._tab_gen.grid_columnconfigure(0, weight=1)

        self._tab_cfg = ctk.CTkFrame(content_frame, fg_color="transparent")
        self._tab_cfg.grid(row=0, column=0, sticky="nsew")
        self._tab_cfg.grid_columnconfigure(0, weight=1)
        self._tab_cfg.grid_remove()  # 初始隐藏

        # ═══════════════════════════════════
        # 构建两个 tab 内容
        # ═══════════════════════════════════
        self._build_tab_generate(self._tab_gen)
        self._build_tab_settings(self._tab_cfg)

        # 初始选中态
        self._switch_tab("gen")

    def _switch_tab(self, tab: str):
        """切换 Tab 显示。"""
        if tab == "gen":
            self._tab_gen.grid()
            self._tab_cfg.grid_remove()
            self._tab_gen_btn.configure(fg_color="#2563eb", hover_color="#1d4ed8")
            self._tab_cfg_btn.configure(fg_color=("gray75", "gray25"), hover_color=("gray65", "gray35"))
        else:
            self._tab_cfg.grid()
            self._tab_gen.grid_remove()
            self._tab_cfg_btn.configure(fg_color="#2563eb", hover_color="#1d4ed8")
            self._tab_gen_btn.configure(fg_color=("gray75", "gray25"), hover_color=("gray65", "gray35"))

    def _build_tab_generate(self, parent):
        """构建「日报生成」Tab。"""
        parent.grid_columnconfigure(0, weight=1)
        row = 0

        # ── 报告日期 ──
        ctk.CTkLabel(parent, text="报告日期", anchor="w").grid(
            row=row, column=0, padx=PAD_X, pady=(PAD_Y, 0), sticky="w"
        )
        row += 1

        date_frame = ctk.CTkFrame(parent, fg_color="transparent")
        date_frame.grid(row=row, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="ew")
        date_frame.grid_columnconfigure(0, weight=1)
        date_frame.grid_columnconfigure(1, weight=0)

        today_str = date.today().isoformat()
        self.date_entry = ctk.CTkEntry(
            date_frame, placeholder_text="YYYY-MM-DD", height=34,
        )
        self.date_entry.insert(0, today_str)
        self.date_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.today_btn = ctk.CTkButton(
            date_frame, text="今天", width=50, height=34,
            command=self._set_today,
        )
        self.today_btn.grid(row=0, column=1, padx=(0, 2))

        self.yesterday_btn = ctk.CTkButton(
            date_frame, text="昨天", width=50, height=34,
            command=self._set_yesterday,
        )
        self.yesterday_btn.grid(row=0, column=2)
        row += 1

        # ── 下班时间 ──
        ctk.CTkLabel(parent, text="下班时间", anchor="w").grid(
            row=row, column=0, padx=PAD_X, pady=(PAD_Y, 0), sticky="w"
        )
        row += 1

        time_frame = ctk.CTkFrame(parent, fg_color="transparent")
        time_frame.grid(row=row, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="ew")
        time_frame.grid_columnconfigure(0, weight=1)
        time_frame.grid_columnconfigure(1, weight=0)

        now_str = datetime.now().strftime("%H:%M")
        self.time_entry = ctk.CTkEntry(
            time_frame, placeholder_text="HH:MM", height=34, width=80,
        )
        self.time_entry.insert(0, now_str)
        self.time_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.now_btn = ctk.CTkButton(
            time_frame, text="现在", width=50, height=34,
            command=self._set_time_now,
        )
        self.now_btn.grid(row=0, column=1, padx=(0, 2))

        self.time_1730_btn = ctk.CTkButton(
            time_frame, text="17:30", width=50, height=34,
            command=lambda: self._set_time("17:30"),
        )
        self.time_1730_btn.grid(row=0, column=2, padx=(0, 2))

        self.time_2030_btn = ctk.CTkButton(
            time_frame, text="20:30", width=50, height=34,
            command=lambda: self._set_time("20:30"),
        )
        self.time_2030_btn.grid(row=0, column=3)
        row += 1

        # ── 分隔线 ──
        ctk.CTkFrame(parent, height=2, fg_color=("gray70", "gray30")).grid(
            row=row, column=0, padx=PAD_X, pady=(PAD_Y, PAD_Y), sticky="ew"
        )
        row += 1

        # ── 生成 + 复制 ──
        gen_row = ctk.CTkFrame(parent, fg_color="transparent")
        gen_row.grid(row=row, column=0, padx=PAD_X, pady=(PAD_Y, PAD_Y), sticky="ew")
        gen_row.grid_columnconfigure(0, weight=2)
        gen_row.grid_columnconfigure(1, weight=1)

        self.generate_btn = ctk.CTkButton(
            gen_row, text="🚀 生成日报", command=self._generate_report,
            height=40, fg_color="#2563eb", hover_color="#1d4ed8",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.generate_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))

        self.copy_btn = ctk.CTkButton(
            gen_row, text="📋 复制", command=self._copy_report,
            height=40, state="disabled",
        )
        self.copy_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        row += 1

        # ── 其他补充输入 ──
        ctk.CTkLabel(parent, text="其他补充（自定义内容）", anchor="w",
                     font=ctk.CTkFont(size=11)).grid(
            row=row, column=0, padx=PAD_X, pady=(PAD_Y, 0), sticky="w"
        )
        row += 1
        self.other_text = ctk.CTkTextbox(parent, height=160, wrap="word",
                                          font=ctk.CTkFont(size=12))
        self.other_text.grid(row=row, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="ew")
        row += 1

        # ── 摘要信息 ──
        self.summary_label = ctk.CTkLabel(
            parent, text="", anchor="w", justify="left",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        )
        self.summary_label.grid(row=row, column=0, padx=PAD_X, pady=(PAD_Y, PAD_Y), sticky="ew")

    def _build_tab_settings(self, parent):
        """构建「设置」Tab。"""
        parent.grid_columnconfigure(0, weight=1)
        row = 0

        # ── 服务器地址 ──
        ctk.CTkLabel(parent, text="服务器地址", anchor="w").grid(
            row=row, column=0, padx=PAD_X, pady=(PAD_Y, 0), sticky="w"
        )
        row += 1
        self.url_entry = ctk.CTkEntry(
            parent, placeholder_text="http://192.168.0.83:8181/redmine/", height=34,
        )
        self.url_entry.grid(row=row, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="ew")
        row += 1

        # ── API Key ──
        key_label_frame = ctk.CTkFrame(parent, fg_color="transparent")
        key_label_frame.grid(row=row, column=0, padx=PAD_X, pady=(PAD_Y, 0), sticky="ew")
        ctk.CTkLabel(key_label_frame, text="API Key", anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        self.key_help_btn = ctk.CTkButton(
            key_label_frame, text="?", width=22, height=22, corner_radius=11,
            fg_color=("gray65", "gray50"), hover_color=("gray55", "gray40"),
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._show_key_help,
        )
        self.key_help_btn.grid(row=0, column=1, padx=(4, 0), sticky="w")
        row += 1

        key_frame = ctk.CTkFrame(parent, fg_color="transparent")
        key_frame.grid(row=row, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="ew")
        key_frame.grid_columnconfigure(0, weight=1)
        key_frame.grid_columnconfigure(1, weight=0)
        key_frame.grid_columnconfigure(2, weight=0)

        self.key_entry = ctk.CTkEntry(
            key_frame, placeholder_text="输入 API 访问密钥", show="•", height=34,
        )
        self.key_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self._key_visible = False
        self.eye_btn = ctk.CTkButton(
            key_frame, text="👁", width=36, height=34,
            command=self._toggle_key_visibility,
        )
        self.eye_btn.grid(row=0, column=1, padx=(0, 2))

        row += 1

        # ── 分隔线 ──
        ctk.CTkFrame(parent, height=2, fg_color=("gray70", "gray30")).grid(
            row=row, column=0, padx=PAD_X, pady=(PAD_Y, PAD_Y), sticky="ew"
        )
        row += 1

        # ── 跟踪项目 ──
        proj_title_row = ctk.CTkFrame(parent, fg_color="transparent")
        proj_title_row.grid(row=row, column=0, padx=PAD_X, pady=(PAD_Y, 0), sticky="ew")
        proj_title_row.grid_columnconfigure(0, weight=1)
        proj_title_row.grid_columnconfigure(1, weight=0)

        ctk.CTkLabel(
            proj_title_row, text="📂 跟踪项目",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        self.proj_fetch_btn = ctk.CTkButton(
            proj_title_row, text="🔄 获取列表",
            command=self._fetch_project_list,
            height=26, width=90,
            font=ctk.CTkFont(size=11),
            fg_color="#2563eb", hover_color="#1d4ed8",
        )
        self.proj_fetch_btn.grid(row=0, column=1, sticky="e")
        row += 1

        # 项目复选框容器（可滚动）
        self.project_frame = ctk.CTkScrollableFrame(
            parent, height=150,
            fg_color=("gray95", "gray15"), corner_radius=6,
        )
        self.project_frame.grid(row=row, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="ew")
        self.project_frame.grid_columnconfigure(0, weight=1)
        row += 1

        # 项目操作按钮行: 全选 / 全不选
        proj_btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        proj_btn_row.grid(row=row, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="ew")
        proj_btn_row.grid_columnconfigure(0, weight=1)
        proj_btn_row.grid_columnconfigure(1, weight=1)

        self.proj_all_btn = ctk.CTkButton(
            proj_btn_row, text="全选", command=self._select_all_projects,
            height=28, font=ctk.CTkFont(size=11),
        )
        self.proj_all_btn.grid(row=0, column=0, sticky="ew", padx=(0, 2))

        self.proj_none_btn = ctk.CTkButton(
            proj_btn_row, text="全不选", command=self._select_no_projects,
            height=28, font=ctk.CTkFont(size=11),
        )
        self.proj_none_btn.grid(row=0, column=1, sticky="ew", padx=(2, 0))
        row += 1

        # ── 分隔线 ──
        ctk.CTkFrame(parent, height=2, fg_color=("gray70", "gray30")).grid(
            row=row, column=0, padx=PAD_X, pady=(PAD_Y, PAD_Y), sticky="ew"
        )
        row += 1

        # ── 测试连接 + 耗时分析 ──
        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.grid(row=row, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="ew")
        btn_row.grid_columnconfigure(0, weight=1)
        btn_row.grid_columnconfigure(1, weight=1)

        self.test_btn = ctk.CTkButton(
            btn_row, text="🔍 测试连接", command=self._test_connection,
            height=34, fg_color="#6b7280", hover_color="#4b5563",
        )
        self.test_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))

        self.perf_btn = ctk.CTkButton(
            btn_row, text="⏱ 耗时分析", command=self._analyze_performance,
            height=34, fg_color="transparent", border_width=1,
            border_color="#f59e0b", text_color="#f59e0b",
            font=ctk.CTkFont(size=12),
        )
        self.perf_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        row += 1

        # ── 审核复核开关 ──
        self.skip_review_var = ctk.BooleanVar(value=False)
        self.skip_review_cb = ctk.CTkCheckBox(
            parent, text="⚡ 跳过审核复核（仅查本人Issue，大幅提速）",
            variable=self.skip_review_var,
            font=ctk.CTkFont(size=11),
        )
        self.skip_review_cb.grid(row=row, column=0, padx=PAD_X, pady=(PAD_Y, 0), sticky="w")
        row += 1

        # ── 保存设置 ──
        self.save_settings_btn = ctk.CTkButton(
            parent, text="💾 保存设置", command=self._save_config,
            height=34, fg_color="#2563eb", hover_color="#1d4ed8",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.save_settings_btn.grid(row=row, column=0, padx=PAD_X, pady=(PAD_Y, PAD_Y), sticky="ew")
        row += 1

        # 存储：{project_id: (name, BooleanVar)}
        self._project_vars: dict[int, tuple[str, ctk.BooleanVar]] = {}
        self._project_labels: list = []

    def _create_preview_panel(self):
        """右侧日报预览区。"""
        frame = ctk.CTkFrame(self, corner_radius=10)
        frame.grid(row=0, column=1, padx=(PAD_X // 2, PAD_X), pady=PAD_Y, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=0)
        frame.grid_rowconfigure(1, weight=1)

        # 标题（动态更新）
        self.preview_title = ctk.CTkLabel(
            frame,
            text="📋 日报预览",
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
        )
        self.preview_title.grid(row=0, column=0, padx=PAD_X, pady=(PAD_Y, 4), sticky="w")

        # 文本区域
        self.preview_text = ctk.CTkTextbox(
            frame,
            wrap="none",
            font=ctk.CTkFont(family="Consolas", size=12),
            activate_scrollbars=True,
        )
        self.preview_text.grid(row=1, column=0, padx=PAD_X, pady=(0, PAD_Y), sticky="nsew")
        self.preview_text._textbox.configure(spacing1=3, spacing2=1, spacing3=3)

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

    def _set_yesterday(self):
        """日期设为昨天。"""
        self.date_entry.delete(0, "end")
        yesterday = date.today() - timedelta(days=1)
        self.date_entry.insert(0, yesterday.isoformat())

    def _set_time_now(self):
        """下班时间设为当前时间。"""
        self.time_entry.delete(0, "end")
        self.time_entry.insert(0, datetime.now().strftime("%H:%M"))

    def _set_time(self, time_str: str):
        """下班时间设为指定值。"""
        self.time_entry.delete(0, "end")
        self.time_entry.insert(0, time_str)

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
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, self.DEFAULT_URL)
        self._project_ids: list[int] = []

        try:
            cfg = load_config()
            if cfg.redmine_url:
                self.url_entry.delete(0, "end")
                self.url_entry.insert(0, cfg.redmine_url)
            if cfg.api_key:
                self.key_entry.delete(0, "end")
                self.key_entry.insert(0, cfg.api_key)
            self._project_ids = cfg.project_ids or []
            self.skip_review_var.set(cfg.skip_review)
            # 自动加载项目列表到界面
            if cfg.redmine_url and cfg.api_key:
                self.after(800, self._fetch_project_list)
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
            self._project_ids = cfg.project_ids or []
            self.skip_review_var.set(cfg.skip_review)
            self._fetch_project_list()
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

            selected_ids = self._get_selected_project_ids()
            data = {
                "redmine_url": self.url_entry.get().strip(),
                "api_key": self.key_entry.get().strip(),
                "timezone": "Asia/Shanghai",
                "output_dir": "./reports",
                "project_ids": selected_ids,
                "skip_review": self.skip_review_var.get(),
            }
            config_path.write_text(
                yaml.dump(data, allow_unicode=True, default_flow_style=False),
                encoding="utf-8",
            )
            self._set_status(f"Key 已保存到 {config_path}")
        except Exception:
            pass  # 保存失败不影响主流程

    # ── 项目选择 ────────────────────────────────────

    def _get_selected_project_ids(self) -> list[int]:
        """从复选框读取当前勾选的项目 ID 列表。"""
        ids: list[int] = []
        for pid, (name, var) in self._project_vars.items():
            if var.get():
                ids.append(pid)
        return ids

    def _populate_project_list(self, all_projects: list[dict], selected_ids: list[int]) -> None:
        """用项目列表填充复选框区域。"""
        # 清空旧内容
        for w in self._project_labels:
            w.destroy()
        self._project_labels.clear()
        self._project_vars.clear()

        selected_set = set(selected_ids)
        row = 0
        for p in all_projects:
            pid = p["id"]
            name = p["name"]
            # 默认勾选：yaml 里有就勾，没有就全勾
            default_checked = pid in selected_set if selected_ids else True
            var = ctk.BooleanVar(value=default_checked)
            cb = ctk.CTkCheckBox(
                self.project_frame,
                text=name,
                variable=var,
                font=ctk.CTkFont(size=11),
            )
            cb.grid(row=row, column=0, padx=4, pady=1, sticky="w")
            self._project_labels.append(cb)
            self._project_vars[pid] = (name, var)
            row += 1

    def _fetch_project_list(self):
        """从 Redmine API 拉取项目列表并刷新界面（用户手动触发）。"""
        url = self.url_entry.get().strip()
        api_key = self.key_entry.get().strip()
        if not url or not api_key:
            self._set_status("请先在「设置」Tab 中输入服务器地址和 API Key")
            return

        self.proj_fetch_btn.configure(state="disabled", text="⏳ 获取中...")
        self._set_status("正在从服务器获取项目列表...")

        try:
            client = RedmineClient(url=url, api_key=api_key)
            user = client.authenticate()
            all_projects = client.list_projects()
            all_projects.sort(key=lambda p: p.get("name", ""))
        except Exception as e:
            self._set_status(f"获取失败: {str(e)[:80]}")
            self.proj_fetch_btn.configure(state="normal", text="🔄 获取列表")
            return

        self._populate_project_list(all_projects, self._project_ids)
        self.proj_fetch_btn.configure(state="normal", text="🔄 获取列表")
        self._set_status(
            f"已加载 {len(all_projects)} 个项目（勾选 {len(self._get_selected_project_ids())} 个）"
        )

    def _select_all_projects(self):
        """全选所有项目。"""
        for pid, (name, var) in self._project_vars.items():
            var.set(True)

    def _select_no_projects(self):
        """取消全选。"""
        for pid, (name, var) in self._project_vars.items():
            var.set(False)

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
        self.test_btn.configure(state="normal", text="🔍 测试连接")
        self._show_progress(False)
        self._set_status("连接测试通过 ✓ — Key 已保存")
        self._set_preview_title("🔍 连接测试")
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

    def _on_test_error(self, msg: str):
        self.test_btn.configure(state="normal", text="🔍 测试连接")
        self._show_progress(False)
        self._set_status("连接测试失败 ✗")
        self._set_preview_title("🔍 连接测试")
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

        # 3. 获取选中的项目（复选框优先，否则用 config.yaml 里保存的）
        selected_ids = self._get_selected_project_ids()
        if not selected_ids and self._project_ids:
            selected_ids = self._project_ids

        # 4. UI 进入加载状态
        self.generate_btn.configure(state="disabled", text="⏳ 获取中...")
        self.copy_btn.configure(state="disabled")
        self._show_progress(True)
        self._set_status(f"正在连接 Redmine...")

        # 5. 后台线程执行
        thread = threading.Thread(
            target=self._do_generate,
            args=(url, api_key, report_date, selected_ids,
                  self.other_text.get("1.0", "end").strip(),
                  self.skip_review_var.get(), False),
            daemon=True,
        )
        thread.start()

    def _analyze_performance(self):
        """耗时分析：生成日报并附带各阶段耗时统计。"""
        url = self.url_entry.get().strip()
        api_key = self.key_entry.get().strip()
        report_date = self.date_entry.get().strip()

        if not url or not api_key:
            messagebox.showwarning("输入错误", "请输入服务器地址和 API Key。")
            return
        if not self._validate_date(report_date):
            messagebox.showwarning("输入错误", f"日期格式错误: {report_date}")
            return

        selected_ids = self._get_selected_project_ids()
        if not selected_ids and self._project_ids:
            selected_ids = self._project_ids

        self.perf_btn.configure(state="disabled", text="⏳ 分析中...")
        self._show_progress(True)
        self._set_status("正在进行耗时分析...")

        thread = threading.Thread(
            target=self._do_generate,
            args=(url, api_key, report_date, selected_ids,
                  self.other_text.get("1.0", "end").strip(),
                  self.skip_review_var.get(), True),
            daemon=True,
        )
        thread.start()

    def _do_generate(self, url: str, api_key: str, report_date: str,
                     project_ids: list[int] | None,
                     custom_other: str = "", skip_review: bool = False,
                     show_timing: bool = False):
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
            proj_ids = project_ids if project_ids else None
            report = client.build_report_data(report_date, project_ids=proj_ids,
                                               skip_review=skip_review)

            # 生成 Markdown
            self._after_status("正在生成日报...")
            content = generate_report(report, custom_other=custom_other, end_time=self.time_entry.get().strip())
            self._report_date = report_date

        except RedmineClientError as e:
            error_msg = str(e)
        except Exception as e:
            error_msg = f"未知错误: {str(e)[:300]}"

        # 回到主线程更新 UI
        if error_msg:
            self.after(0, lambda: self._on_generate_error(error_msg))
        else:
            self.after(0, lambda: self._on_generate_success(report, content, show_timing))

    def _on_generate_success(self, report, content: str, show_timing: bool = False):
        """生成成功：更新预览。show_timing=True 时追加耗时分析。"""
        self._report_content = content
        self._set_preview_title("📋 日报预览")

        # 更新预览区 + 分节着色
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", content)

        # 配置颜色标签
        section_colors = {
            "1）": "#3b82f6",  # 蓝 — 新增
            "2）": "#10b981",  # 绿 — 复测
            "3）": "#ef4444",  # 红 — 审核/复核
            "4）": "#c026d3",  # 洋红 — 其他
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

        # ── 耗时分析（仅 show_timing 模式）──
        timing = getattr(report, "timing", {}) if show_timing else {}
        if show_timing and timing:
            self.preview_text.configure(state="normal")
            self.preview_text.insert("end", "\n\n")
            self.preview_text.insert("end", "──────── 耗时统计 ────────\n")
            lines = [
                f"认证:          {timing.get('auth', '?')}s",
                f"filter查询:    {timing.get('filter', '?')}s  ({timing.get('filter_queries', '?')}次请求, {timing.get('filter_candidates', '?')}个候选)",
                f"预分类:        {timing.get('preclassify', '?')}s  (新增{timing.get('preclassify_new', '?')} + 待验证{timing.get('preclassify_pending', '?')})",
                f"搜索API预筛:   {timing.get('search_api', '?')}s  (命中{timing.get('search_hit', '?')}个, 筛掉{timing.get('search_skipped', '?')}个无关){'  FAIL: '+timing.get('search_error','') if timing.get('search_error') else ''}",
                f"journal验证:   {timing.get('journal', '?')}s  (检查{timing.get('journal_checked', '?')}个, 确认{timing.get('journal_confirmed', '?')}个)",
                f"─────────────────────────",
                f"总耗时:        {timing.get('build_total', timing.get('total', '?'))}s",
            ]
            for line in lines:
                self.preview_text.insert("end", line + "\n")
            candidates = timing.get("candidates_detail", [])
            if candidates:
                self.preview_text.insert("end", "\n")
                self.preview_text.insert("end", f"──────── filter候选 Issue（{len(candidates)}个）────────\n")
                for line in candidates[:200]:
                    self.preview_text.insert("end", line + "\n")
            # 搜索 API 调试信息
            search_debug = timing.get("search_debug", "")
            if search_debug:
                self.preview_text.insert("end", "\n")
                self.preview_text.insert("end", "──────── 搜索API原始返回 ────────\n")
                for line in search_debug.split("\n")[:20]:
                    self.preview_text.insert("end", line + "\n")
            # journal 原始内容调试
            journal_debug = timing.get("journal_debug", [])
            if journal_debug:
                self.preview_text.insert("end", "\n")
                self.preview_text.insert("end", "──────── 已确认Issue的journal内容 ────────\n")
                for line in journal_debug[:60]:
                    self.preview_text.insert("end", line + "\n")

        # 更新摘要
        timing_str = f" | 总耗时{timing.get('build_total', '?')}s" if (show_timing and timing) else ""
        summary = (
            f"用户: {report.user_name}\n"
            f"问题: {report.total_issues}个 | "
            f"项目: {report.project_count}个"
            f"{timing_str}"
        )
        self.summary_label.configure(text=summary)

        # 恢复 UI
        self.generate_btn.configure(state="normal", text="🚀 生成日报")
        self.copy_btn.configure(state="normal")
        self.perf_btn.configure(state="normal", text="⏱ 耗时分析")
        self._show_progress(False)
        self._set_status(f"日报已生成 — {report.date} | "
                         f"{report.total_issues}个问题")

    def _on_generate_error(self, error_msg: str):
        """生成失败：显示错误。"""
        self.generate_btn.configure(state="normal", text="🚀 生成日报")
        self.perf_btn.configure(state="normal", text="⏱ 耗时分析")
        self._show_progress(False)
        self._set_status("生成失败")
        messagebox.showerror("生成失败", error_msg)

    def _copy_report(self):
        """复制日报到剪贴板（带颜色格式）。"""
        if not self._report_content:
            return
        html = self._report_to_html(self._report_content)
        try:
            self._write_clipboard_html(self._report_content, html)
            self._set_status("日报已复制到剪贴板 ✓（带格式）")
        except Exception:
            # 回退到纯文本
            self.clipboard_clear()
            self.clipboard_append(self._report_content)
            self._set_status("日报已复制到剪贴板 ✓")

    @staticmethod
    def _report_to_html(content: str) -> str:
        """将纯文本日报转为带颜色的 HTML。"""
        SECTION_COLORS = {
            "1）": "#3b82f6",  # 蓝 — 新增
            "2）": "#10b981",  # 绿 — 复测
            "3）": "#ef4444",  # 红 — 审核/复核
            "4）": "#c026d3",  # 洋红 — 其他
        }
        lines = content.split("\n")
        html_lines = []
        current_color: str | None = None

        for line in lines:
            escaped = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

            color = None
            for prefix, c in SECTION_COLORS.items():
                if escaped.startswith(prefix):
                    color = c
                    current_color = c
                    break

            if color is None and escaped.strip() and current_color:
                color = current_color  # 条目沿用节的颜色

            if color:
                html_lines.append(f'<span style="color:{color}">{escaped}</span>')
            else:
                html_lines.append(escaped)

        body = "\n".join(html_lines)
        return (
            f'<pre style="font-family:Consolas,monospace;font-size:12pt;'
            f'line-height:1.6;margin:0">{body}</pre>'
        )

    @staticmethod
    def _write_clipboard_html(plain_text: str, html: str):
        """使用 Windows Clipboard API 同时写入 HTML 和纯文本。"""
        # 构建 HTML Format 格式（Windows 剪贴板标准）
        pre = (
            "Version:0.9\r\n"
            "StartHTML:0000000000\r\n"
            "EndHTML:0000000000\r\n"
            "StartFragment:0000000000\r\n"
            "EndFragment:0000000000\r\n"
        )
        html_doc = (
            "<html><body>\r\n"
            "<!--StartFragment-->\r\n"
            f"{html}\r\n"
            "<!--EndFragment-->\r\n"
            "</body></html>"
        )

        pre_b = pre.encode("utf-8")
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

            # HTML 格式
            CF_HTML = win32clipboard.RegisterClipboardFormat("HTML Format")
            win32clipboard.SetClipboardData(CF_HTML, clipboard_html)

            # 纯文本格式（CF_UNICODETEXT = 13）
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, plain_text)
        finally:
            win32clipboard.CloseClipboard()

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
                upd_beijing = ""
                try:
                    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
                    tz_cn = _tz(_td(hours=8))
                    if isinstance(upd, _dt):
                        dt_val = upd
                        if dt_val.tzinfo is None:
                            dt_val = dt_val.replace(tzinfo=_tz.utc)
                        cn = dt_val.astimezone(tz_cn)
                        time_str = cn.strftime("%H:%M")
                        upd_beijing = cn.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        s = str(upd)
                        if "T" in s:
                            s_clean = s.replace("Z", "+00:00")
                            cn = _dt.fromisoformat(s_clean).astimezone(tz_cn)
                            time_str = cn.strftime("%H:%M")
                            upd_beijing = cn.strftime("%Y-%m-%d %H:%M:%S")
                        elif " " in s:
                            time_str = s.split(" ")[1][:5]
                            upd_beijing = s
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
                lines.append(f"| updated_on | {upd_beijing or upd} |")
                lines.append(f"| time(HH:MM) | {time_str} |")
                lines.append("")

            lines.append("---\n")
            lines.append("## [4] 分类结果\n")
            lines.append("| # | 分类 | tracker | status | author |")
            lines.append("|------|------|------|------|------|")
            for _i, (_src, iss) in enumerate(all_issues, 1):
                author_id_val = getattr(iss.author, 'id', 0) if hasattr(iss, 'author') else 0
                status_val = getattr(iss.status, 'name', '')
                tracker_val = getattr(iss.tracker, 'name', '')
                created_raw = getattr(iss, 'created_on', None)
                created_date = ""
                try:
                    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
                    tz_cn = _tz(_td(hours=8))
                    if isinstance(created_raw, _dt):
                        dt_val = created_raw
                        if dt_val.tzinfo is None:
                            dt_val = dt_val.replace(tzinfo=_tz.utc)
                        created_date = dt_val.astimezone(tz_cn).strftime("%Y-%m-%d")
                    elif created_raw:
                        s = str(created_raw)
                        if "T" in s:
                            s_clean = s.replace("Z", "+00:00")
                            created_date = _dt.fromisoformat(s_clean).astimezone(tz_cn).strftime("%Y-%m-%d")
                        else:
                            created_date = s[:10]
                except Exception:
                    pass
                is_mine = author_id_val == user_id
                # 简易分类（无 journal 数据，has_status_change 默认 True 给复测）
                if tracker_val == "支持" and is_mine and created_date == report_date:
                    cat = "新增"
                elif status_val == "新建" and is_mine and created_date == report_date:
                    cat = "新增"
                elif status_val != "新建" and is_mine:
                    cat = "复测(*)"  # * 需 journal 确认状态变更
                elif not is_mine and status_val:
                    cat = "审核/复核"
                else:
                    cat = "丢弃"
                lines.append(f"| {_i} | **{cat}** | {tracker_val} | {status_val} | {'本人' if is_mine else author_id_val} |")
            lines.append("")
            lines.append("> 复测(*) = 需 journal 验证确认当天是否有状态变更")
            lines.append("")
            lines.append("## [5] 汇总\n")
            lines.append(f"- 查询到 **{len(all_issues)}** 个 Issue\n")
            lines.append("> 如果上面 Issue 数据正常但日报为空，截图发给我。")

            self.after(0, lambda: self._show_diag_result("\n".join(lines)))

        threading.Thread(target=_do, daemon=True).start()

    def _show_diag_result(self, text: str):
        """在预览区显示诊断结果。"""
        self.diag_btn.configure(state="normal", text="🔬 数据诊断")
        self._show_progress(False)
        self._set_status("诊断完成 — 请查看右侧结果")
        self._set_preview_title("🔬 数据诊断")
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", text)
        self.preview_text.configure(state="disabled")

    # ── UI 辅助方法 ──────────────────────────────────

    def _set_status(self, text: str):
        """更新状态栏文字。"""
        self.status_label.configure(text=text)

    def _set_preview_title(self, text: str):
        """更新右侧预览区标题。"""
        self.preview_title.configure(text=text)

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
