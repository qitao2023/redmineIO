"""配置加载 — YAML 文件 + 环境变量覆盖。"""

import os
import sys
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """配置错误。"""

    pass


def _get_bundled_path() -> Path | None:
    """获取 PyInstaller 打包时内置 config.yaml 的路径。"""
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
        p = base / "config.yaml"
        if p.exists():
            return p
    return None


class Config:
    """Redmine 日报工具配置。

    加载优先级（后覆盖前）：
    1. 内置默认值
    2. exe 内置 config.yaml（PyInstaller 打包时）
    3. 外部 config.yaml（exe 同目录 或 ~/.redmine_report/）
    4. 环境变量
    """

    @classmethod
    def _search_paths(cls) -> list[Path]:
        paths = []
        # 用户设置目录最优先（auto-save 写这里）
        paths.append(Path.home() / ".redmine_report" / "config.yaml")
        # 兼容旧版：exe 同目录
        if getattr(sys, "frozen", False):
            paths.append(Path(sys.executable).parent / "config.yaml")
        paths.extend([
            Path("config.yaml"),
            Path("/etc/redmine_report/config.yaml"),
        ])
        # 内置配置兜底
        bundled = _get_bundled_path()
        if bundled:
            paths.append(bundled)
        return paths

    def __init__(
        self,
        config_path: str | Path | None = None,
        redmine_url: str | None = None,
        api_key: str | None = None,
    ):
        """加载配置。

        Args:
            config_path: 显式指定的配置文件路径（优先）。
            redmine_url: 代码中传入的 URL（测试用）。
            api_key: 代码中传入的 Key（测试用）。
        """
        self.data: dict[str, Any] = self._defaults()

        # 1. 从 YAML 加载
        yaml_path = config_path or self._find_config()
        if yaml_path and Path(yaml_path).exists():
            self._load_yaml(Path(yaml_path))

        # 2. 环境变量覆盖
        self._apply_env_overrides()

        # 3. 参数覆盖（最高优先级）
        if redmine_url:
            self.data["redmine_url"] = redmine_url
        if api_key:
            self.data["api_key"] = api_key

        # 4. 验证必要配置
        self._validate()

    @staticmethod
    def _defaults() -> dict[str, Any]:
        return {
            "redmine_url": "",
            "api_key": "",
            "timezone": "Asia/Shanghai",
            "output_dir": "./reports",
            "requests_verify": True,
            "requests_timeout": 30,
            "project_ids": [],  # 手动指定项目 ID 列表，空列表=自动获取
            "skip_review": False,  # True=跳过审核复核，只查本人创建/经办的 Issue
            "review_strict": True,  # True=审核复核仅计入状态/指派变更，纯评论不算
            "support_always_new": False,  # True=支持类Issue始终显示初始状态（新建）
            "report_with_numbers": True,  # True=节号带数量 + issue带序号
        }

    def _find_config(self) -> Path | None:
        for p in self._search_paths():
            if p.exists():
                return p
        return None

    def _load_yaml(self, path: Path) -> None:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        # 支持顶层平铺格式
        if "redmine" in raw:
            rm = raw["redmine"]
            if "url" in rm:
                self.data["redmine_url"] = rm["url"]
            if "api_key" in rm:
                self.data["api_key"] = rm["api_key"]
            if "timezone" in rm:
                self.data["timezone"] = rm["timezone"]
            if "requests" in rm:
                req = rm["requests"]
                if "verify" in req:
                    self.data["requests_verify"] = req["verify"]
                if "timeout" in req:
                    self.data["requests_timeout"] = req["timeout"]

        if "report" in raw:
            rp = raw["report"]
            if "output_dir" in rp:
                self.data["output_dir"] = rp["output_dir"]
            if "project_ids" in rp:
                self.data["project_ids"] = rp["project_ids"]
            if "skip_review" in rp:
                self.data["skip_review"] = rp["skip_review"]
            if "review_strict" in rp:
                self.data["review_strict"] = rp["review_strict"]
            if "support_always_new" in rp:
                self.data["support_always_new"] = rp["support_always_new"]
            if "report_with_numbers" in rp:
                self.data["report_with_numbers"] = rp["report_with_numbers"]

        # 也支持顶层平铺字段
        for key in ("redmine_url", "api_key", "timezone", "output_dir", "project_ids",
                     "skip_review", "review_strict", "support_always_new", "report_with_numbers"):
            if key in raw and raw[key]:
                self.data[key] = raw[key]

        # 项目名称映射（用于离线还原列表）
        if "project_names" in raw:
            self.data["project_names"] = raw["project_names"]

    def _apply_env_overrides(self) -> None:
        env_map = {
            "REDMINE_URL": "redmine_url",
            "REDMINE_API_KEY": "api_key",
            "REDMINE_TIMEZONE": "timezone",
        }
        for env_key, cfg_key in env_map.items():
            val = os.environ.get(env_key)
            if val:
                self.data[cfg_key] = val

    def _validate(self) -> None:
        if not self.data["redmine_url"]:
            raise ConfigError(
                "未配置 Redmine URL。请设置 config.yaml 中的 redmine_url "
                "或环境变量 REDMINE_URL。"
            )
        # API Key 允许为空（用户在 GUI 中手动输入）

    @property
    def redmine_url(self) -> str:
        return self.data["redmine_url"]

    @property
    def api_key(self) -> str:
        return self.data["api_key"]

    @property
    def timezone(self) -> str:
        return self.data["timezone"]

    @property
    def output_dir(self) -> str:
        return self.data["output_dir"]

    @property
    def requests_verify(self) -> bool:
        return self.data["requests_verify"]

    @property
    def requests_timeout(self) -> int:
        return self.data["requests_timeout"]

    @property
    def project_ids(self) -> list[int]:
        """手动指定的项目 ID 列表。空列表 = 自动从 Redmine 获取。"""
        ids = self.data.get("project_ids", [])
        if ids is None:
            return []
        if isinstance(ids, list):
            return [int(i) for i in ids]
        return []

    @property
    def skip_review(self) -> bool:
        """是否跳过审核复核（只查本人创建/经办的 Issue）。"""
        return bool(self.data.get("skip_review", False))

    @property
    def review_strict(self) -> bool:
        """审核复核是否仅计入状态/指派变更（纯评论不算）。"""
        return bool(self.data.get("review_strict", False))

    @property
    def support_always_new(self) -> bool:
        """支持类 Issue 是否始终显示初始状态（新建），不跟随状态变更。"""
        return bool(self.data.get("support_always_new", False))

    @property
    def report_with_numbers(self) -> bool:
        """日报是否在节号后显示数量，issue 前带序号。"""
        return bool(self.data.get("report_with_numbers", False))


def load_config(
    config_path: str | Path | None = None,
    redmine_url: str | None = None,
    api_key: str | None = None,
) -> Config:
    """便捷函数：加载并返回 Config 对象。"""
    return Config(
        config_path=config_path,
        redmine_url=redmine_url,
        api_key=api_key,
    )
