"""Redmine API 封装 — 所有 API 调用的唯一入口。"""

import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone as _tz
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

# 预计算北京时间时区，避免每次调用时重复创建
_BEIJING_TZ = _tz(timedelta(hours=8))


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
        """获取指定用户在指定日期处理过的 Issues。"""
        dt = datetime.strptime(report_date, "%Y-%m-%d")
        yesterday = (dt - timedelta(days=1)).strftime("%Y-%m-%d")

        seen: set[int] = set()
        candidates: list = []
        seen_lock = threading.Lock()

        def _query_date(date_str, **extra_filters):
            """查询一页 Issue，线程安全地加入 candidates。"""
            try:
                for issue in self._redmine.issue.filter(
                    updated_on=date_str,
                    status_id="*",
                    sort="updated_on:desc",
                    limit=limit,
                    **extra_filters,
                ):
                    with seen_lock:
                        if issue.id not in seen:
                            seen.add(issue.id)
                            candidates.append(issue)
            except BaseRedmineError as e:
                logger.warning("查询 %s 失败: %s", date_str, str(e)[:100])

        # 并行执行所有 filter 查询（原来串行 ~3-4 次 → 同时发出）
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(_query_date, report_date, author_id=user_id),
                executor.submit(_query_date, report_date, assigned_to_id=user_id),
                executor.submit(_query_date, report_date),
            ]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    pass

        # ── 预分类：从 filter 结果直接判定「新增」类 Issue ──
        # 这些无需拉取 journals，直接纳入结果
        result: list[dict[str, Any]] = []
        verified: set[int] = set()
        need_journal_check: list = []

        for issue in candidates:
            author_id = getattr(issue.author, "id", 0) if hasattr(issue, "author") else 0
            status_name = getattr(issue.status, "name", "")
            tracker_name = getattr(issue.tracker, "name", "")
            created_on = self._to_beijing_date(getattr(issue, "created_on", None))

            # 新增 — 本人创建 + 当天创建，无需 journal 验证
            if author_id == user_id and created_on == report_date:
                data = self._extract_issue_data(issue)
                if data["issue_id"] not in verified:
                    verified.add(data["issue_id"])
                    result.append(data)
                continue

            # 其余 Issue 需要 journal 验证
            need_journal_check.append(issue)

        # 只对需要验证的 Issue 拉取 journals（原来对所有候选都拉）
        self._check_journals_concurrent(
            need_journal_check, user_id, report_date, verified, result
        )

        result.sort(key=lambda x: x.get("updated_on", ""), reverse=True)
        return result

    def _check_journals_concurrent(
        self,
        issues: list,
        user_id: int,
        report_date: str,
        seen: set[int],
        result: list[dict[str, Any]],
        max_workers: int = 20,
    ):
        """并发检查 Issue 的 journal，20 线程 + 重试 1 次。"""

        def _fetch_detailed(issue_id: int) -> tuple[Any, list] | None:
            for attempt in range(2):
                try:
                    detailed = self._redmine.issue.get(issue_id, include="journals")
                    journals = list(getattr(detailed, "journals", []))
                    return detailed, journals
                except BaseRedmineError:
                    if attempt < 1:
                        continue
            return None

        def _check_one(issue):
            """检查单个 Issue 的 journals，验证用户当天是否有操作。"""
            result_item = _fetch_detailed(issue.id)
            if result_item is None:
                return None
            detailed, journals = result_item

            # 遍历 journals 查找用户当天的操作记录
            for journal in journals:
                try:
                    journal_user = getattr(journal, "user", None)
                    journal_user_id = getattr(journal_user, "id", None) if journal_user else None
                except Exception:
                    continue
                if str(journal_user_id) != str(user_id):
                    continue
                journal_date = self._to_beijing_date(
                    getattr(journal, "created_on", None)
                )
                if journal_date == report_date:
                    return self._extract_issue_data(detailed)
            return None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_check_one, iss): iss for iss in issues}
            for future in as_completed(futures):
                try:
                    data = future.result()
                except Exception:
                    continue
                if data is not None and data["issue_id"] not in seen:
                    seen.add(data["issue_id"])
                    result.append(data)

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
            "author_id": getattr(issue.author, "id", 0) if hasattr(issue, "author") else 0,
            "created_on": self._to_beijing_date(getattr(issue, "created_on", None)),
        }

    @staticmethod
    def _to_beijing_date(ts: Any) -> str:
        """将时间戳转为北京时间日期字符串 YYYY-MM-DD。"""
        if not ts:
            return ""
        try:
            if isinstance(ts, datetime):
                dt_val = ts
                if dt_val.tzinfo is None:
                    dt_val = dt_val.replace(tzinfo=_tz.utc)
                return dt_val.astimezone(_BEIJING_TZ).strftime("%Y-%m-%d")
            s = str(ts)
            if "T" in s:
                s_clean = s.replace("Z", "+00:00")
                return datetime.fromisoformat(s_clean).astimezone(_BEIJING_TZ).strftime("%Y-%m-%d")
            return s[:10]
        except (ValueError, IndexError):
            return str(ts)[:10]

    @staticmethod
    def _extract_time(timestamp: Any) -> str:
        """从时间戳提取 HH:MM，转为北京时间 (UTC+8)。"""
        if not timestamp:
            return ""
        try:
            if isinstance(timestamp, datetime):
                dt_val = timestamp
                if dt_val.tzinfo is None:
                    dt_val = dt_val.replace(tzinfo=_tz.utc)
                cn_time = dt_val.astimezone(_BEIJING_TZ)
                return cn_time.strftime("%H:%M")

            s = str(timestamp)
            if "T" in s:
                s_clean = s.replace("Z", "+00:00")
                dt_val = datetime.fromisoformat(s_clean)
                cn_time = dt_val.astimezone(_BEIJING_TZ)
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

        user_info = self.authenticate()
        user_id = user_info["id"]

        raw_issues = self.get_issues_by_date(report_date, user_id)
        print(
            f"找到 {len(raw_issues)} 个 Issue（{report_date}）",
            file=sys.stderr,
        )

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
                    author_id=raw.get("author_id", 0),
                    created_on=raw.get("created_on", ""),
                )
            )

        entries.sort(key=lambda e: e.time_str if e.time_str else "99:99")

        unique_projects = set(e.project_name for e in entries if e.project_name)

        try:
            dt_val = datetime.strptime(report_date, "%Y-%m-%d")
            weekday_idx = dt_val.weekday()
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
            current_user_id=user_id,
            report_date=report_date,
        )
