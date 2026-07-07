"""Redmine API 封装 — 所有 API 调用的唯一入口。"""

import logging
import sys
from datetime import date, datetime
from typing import Any

from redminelib import Redmine
from redminelib.exceptions import (
    AuthError,
    BaseRedmineError,
    ResourceNotFoundError,
)

from .models import DailyReport, IssueEntryData

logger = logging.getLogger(__name__)

# 中文周几映射
_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class RedmineClientError(Exception):
    """Redmine 客户端错误。"""
    pass


class RedmineClient:
    """封装 Redmine REST API 调用。"""

    def __init__(self, url: str, api_key: str, timeout: int = 30):
        self.url = url.rstrip("/")
        self._api_key = api_key
        self._redmine: Redmine | None = None

        try:
            self._redmine = Redmine(
                self.url,
                key=api_key,
                requests={"timeout": timeout},
            )
        except BaseRedmineError as e:
            raise RedmineClientError(
                f"无法连接到 Redmine 服务器: {self.url}\n{str(e)[:200]}"
            ) from e

    def authenticate(self) -> dict[str, Any]:
        """验证 API Key 有效性，返回当前用户信息。"""
        try:
            user = self._redmine.user.get("current")

            # 尝试多种方式获取中文名
            name = ""
            lastname = getattr(user, "lastname", "")
            firstname = getattr(user, "firstname", "")
            if lastname or firstname:
                name = f"{lastname}{firstname}"
            if not name:
                name = getattr(user, "name", "") or getattr(user, "login", "")

            return {
                "id": user.id,
                "login": getattr(user, "login", ""),
                "name": name,
                "mail": getattr(user, "mail", ""),
            }
        except AuthError:
            raise RedmineClientError(
                "API Key 验证失败，请检查 Key 是否正确。\n"
                "获取方式：Redmine → 我的账号 → API 访问键 → 显示。"
            )
        except BaseRedmineError as e:
            raise RedmineClientError(f"认证过程出错: {str(e)[:200]}") from e

    def list_projects(self) -> list[dict[str, Any]]:
        """列出当前用户可访问的所有项目。"""
        try:
            projects = []
            for p in self._redmine.project.all():
                projects.append({"id": p.id, "name": p.name, "identifier": p.identifier})
            return projects
        except BaseRedmineError as e:
            raise RedmineClientError(f"获取项目列表失败: {str(e)[:200]}") from e

    def get_issues_by_date(
        self, report_date: str, user_id: int, limit: int = 300
    ) -> list[dict[str, Any]]:
        """获取指定用户在指定日期处理过的 Issues。

        查询策略：
        1. 用户作为作者 + updated_on = 报告日期
        2. 用户作为指派 + updated_on = 报告日期
        合并去重，按 updated_on 排序。
        """
        seen: set[int] = set()
        result: list[dict[str, Any]] = []

        # 策略1: author_id（用户创建的 Issue，当天有更新）
        try:
            for issue in self._redmine.issue.filter(
                author_id=user_id,
                updated_on=report_date,
                sort="updated_on:desc",
                limit=limit,
            ):
                if issue.id not in seen:
                    seen.add(issue.id)
                    result.append(self._extract_issue_data(issue))
        except BaseRedmineError as e:
            logger.warning("按 author_id 查询 Issue 失败: %s", str(e)[:100])

        # 策略2: assigned_to_id（指派给用户的 Issue，当天有更新）
        try:
            for issue in self._redmine.issue.filter(
                assigned_to_id=user_id,
                updated_on=report_date,
                sort="updated_on:desc",
                limit=limit,
            ):
                if issue.id not in seen:
                    seen.add(issue.id)
                    result.append(self._extract_issue_data(issue))
        except BaseRedmineError as e:
            logger.warning("按 assigned_to_id 查询 Issue 失败: %s", str(e)[:100])

        # 按时间排序（晚的在上，对应原格式）
        result.sort(key=lambda x: x.get("updated_on", ""), reverse=True)

        return result

    def _extract_issue_data(self, issue: Any) -> dict[str, Any]:
        """从 Issue 资源提取结构化数据。"""
        updated = getattr(issue, "updated_on", "")
        time_str = self._extract_time(updated)

        return {
            "issue_id": issue.id,
            "subject": issue.subject or "",
            "project_name": getattr(issue.project, "name", ""),
            "tracker_name": getattr(issue.tracker, "name", ""),
            "status_name": getattr(issue.status, "name", ""),
            "priority_name": getattr(issue.priority, "name", ""),
            "updated_on": str(updated) if updated else "",
            "time_str": time_str,
        }

    @staticmethod
    def _extract_time(timestamp: Any) -> str:
        """从时间戳提取 HH:MM，转为北京时间 (UTC+8)。"""
        if not timestamp:
            return ""
        try:
            from datetime import timedelta, timezone
            tz_cn = timezone(timedelta(hours=8))

            if isinstance(timestamp, datetime):
                dt = timestamp
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                cn_time = dt.astimezone(tz_cn)
                return cn_time.strftime("%H:%M")

            s = str(timestamp)
            # 尝试解析 ISO 格式: 2026-07-06T10:30:00Z
            if "T" in s:
                s_clean = s.replace("Z", "+00:00")
                dt = datetime.fromisoformat(s_clean)
                cn_time = dt.astimezone(tz_cn)
                return cn_time.strftime("%H:%M")
            if " " in s:
                return s.split(" ")[1][:5]
        except (ValueError, IndexError):
            pass
        return ""

    def build_report_data(self, report_date: str | None = None) -> DailyReport:
        """编排所有 API 调用，构建完整的 DailyReport 对象。"""
        if report_date is None:
            report_date = date.today().isoformat()

        # 1. 验证用户
        user_info = self.authenticate()
        user_id = user_info["id"]

        # 2. 获取当日 Issues
        raw_issues = self.get_issues_by_date(report_date, user_id)
        print(
            f"找到 {len(raw_issues)} 个 Issue（{report_date}）",
            file=sys.stderr,
        )

        # 3. 组装 IssueEntryData 列表
        entries: list[IssueEntryData] = []
        seen_ids: set[int] = set()

        for raw in raw_issues:
            issue_id = raw["issue_id"]
            if issue_id in seen_ids:
                continue
            seen_ids.add(issue_id)

            entries.append(
                IssueEntryData(
                    issue_id=issue_id,
                    issue_subject=raw["subject"],
                    project_name=raw["project_name"],
                    tracker_name=raw["tracker_name"],
                    status_name=raw["status_name"],
                    priority_name=raw["priority_name"],
                    updated_on=raw["updated_on"],
                    time_str=raw["time_str"],
                )
            )

        # 4. 按时间排序（从早到晚，对应原报告格式）
        entries.sort(key=lambda e: e.time_str if e.time_str else "99:99")

        # 5. 汇总
        unique_projects = set(e.project_name for e in entries if e.project_name)

        # 6. 周几
        try:
            dt = datetime.strptime(report_date, "%Y-%m-%d")
            weekday_idx = dt.weekday()
        except ValueError:
            weekday_idx = 0
        weekday_cn = _WEEKDAY_CN[weekday_idx] if 0 <= weekday_idx < 7 else ""

        return DailyReport(
            user_name=user_info["name"],
            date=report_date,
            weekday_cn=weekday_cn,
            entries=entries,
            total_issues=len(entries),
            project_count=len(unique_projects),
        )
