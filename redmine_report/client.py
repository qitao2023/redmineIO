"""Redmine API 封装 — 所有 API 调用的唯一入口。"""

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any

from redminelib import Redmine
from redminelib.exceptions import (
    AuthError,
    BaseRedmineError,
    ResourceNotFoundError,
)

from .models import DailyReport, IssueEntryData

logger = logging.getLogger(__name__)

# 诊断日志：写到 exe 同目录，每次调用动态取路径
import os as _os


def _diag(msg: str) -> None:
    """写诊断日志到文件（exe 同目录下的 redmine_diag.log）。"""
    try:
        log_path = _os.path.join(
            _os.path.dirname(_os.path.abspath(sys.executable if getattr(sys, "frozen", False) else _os.getcwd())),
            "redmine_diag.log",
        )
        with open(log_path, "a", encoding="utf-8") as f:
            from datetime import datetime as _dt
            f.write(f"[{_dt.now().strftime('%H:%M:%S')}] {msg}\n")
            f.flush()
    except Exception:
        pass

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
        """获取指定用户在指定日期处理过的 Issues。"""
        _diag(f"=== 开始查询 report_date={report_date} user_id={user_id} ===")
        _diag(f"日志路径: {_os.path.join(_os.path.dirname(_os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else _os.getcwd())), 'redmine_diag.log')}")

        # 用两天单日期查询代替范围查询：Redmine 不支持 >< 语法，但支持单日期
        # 昨天 + 今天，覆盖 UTC 时区偏差（北京时间 0-8 点 = UTC 前一天）
        dt = datetime.strptime(report_date, "%Y-%m-%d")
        yesterday = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
        dates_to_query = [report_date, yesterday]
        _diag(f"updated_on 查询日期: {dates_to_query}")

        seen: set[int] = set()
        candidates: list = []

        def _query_date(date_str, **extra_filters):
            """查询单日 Issue，合并去重到 candidates。"""
            try:
                for issue in self._redmine.issue.filter(
                    updated_on=date_str,
                    status_id="*",  # 必须！默认只返回 open，加 * 才包含已关闭
                    sort="updated_on:desc",
                    limit=limit,
                    **extra_filters,
                ):
                    if issue.id not in seen:
                        seen.add(issue.id)
                        candidates.append(issue)
            except BaseRedmineError as e:
                logger.warning("查询 %s 失败: %s", date_str, str(e)[:100])

        # 策略1: author_id（两个日期各查一次）
        for d in dates_to_query:
            _query_date(d, author_id=user_id)

        # 策略2: assigned_to_id（两个日期各查一次）
        for d in dates_to_query:
            _query_date(d, assigned_to_id=user_id)

        # 策略3: 所有 Issue（两个日期各查一次）
        for d in dates_to_query:
            _query_date(d)
            if len(candidates) > limit + 50:
                break

        # ── 并发 journal 验证：只看今天当事人有操作记录的 ──
        _diag(f"候选 Issue 共 {len(candidates)} 个:")
        for iss in candidates:
            _diag(f"  #{iss.id} "
                  f"tracker={getattr(iss.tracker, 'name', '?')} "
                  f"status={getattr(iss.status, 'name', '?')} "
                  f"author_id={getattr(iss.author, 'id', '?') if hasattr(iss, 'author') else '?'}")
        result: list[dict[str, Any]] = []
        verified: set[int] = set()
        self._check_journals_concurrent(candidates, user_id, report_date,
                                        verified, result)
        passed_ids = {r["issue_id"] for r in result}
        _diag(f"验证结果: 通过 {len(result)} / 候选 {len(candidates)}")
        for iss in candidates:
            status = "PASS" if iss.id in passed_ids else "FAIL"
            _diag(f"  {status} #{iss.id}")

        # 按时间排序（晚的在上，对应原格式）
        result.sort(key=lambda x: x.get("updated_on", ""), reverse=True)

        return result

    def _check_journals_concurrent(
        self,
        issues: list,
        user_id: int,
        report_date: str,
        seen: set[int],
        result: list[dict[str, Any]],
        max_workers: int = 5,
    ):
        """并发检查 Issue 的 journal，找出用户当天操作过的。

        降低并发（5 线程）+ 失败重试 2 次，避免多线程共享连接导致静默丢数据。
        """

        def _fetch_detailed(issue_id: int) -> tuple[Any, list] | None:
            """获取 Issue 详情 + journals 列表，失败重试 2 次。"""
            for attempt in range(3):
                try:
                    detailed = self._redmine.issue.get(issue_id)
                    journals = list(detailed.journals)
                    return detailed, journals
                except BaseRedmineError:
                    if attempt < 2:
                        continue
            return None

        def _check_one(issue):
            result = _fetch_detailed(issue.id)
            if result is None:
                _diag(f"  FAIL #{issue.id}: API 获取详情失败(重试3次)")
                return None
            detailed, journals = result

            try:
                author_id = getattr(detailed.author, "id", 0) if hasattr(detailed, "author") else 0
                status_name = getattr(detailed.status, "name", "")
            except Exception:
                _diag(f"  FAIL #{issue.id}: 读取 author/status 异常")
                return None

            # 新建 + 本人 + 当日创建 → 无需 journal 验证，直接通过
            if author_id == user_id and status_name == "新建":
                created_date = self._to_beijing_date(
                    getattr(detailed, "created_on", None)
                )
                if created_date == report_date:
                    return self._extract_issue_data_safe(detailed)
                # created_on 不是今天，继续走 journal 检查
            my_dates: list[str] = []
            my_count = 0
            for journal in journals:
                try:
                    journal_user = getattr(journal, "user", None)
                    journal_user_id = getattr(journal_user, "id", None) if journal_user else None
                except Exception:
                    continue
                if str(journal_user_id) != str(user_id):
                    continue
                my_count += 1
                journal_date = self._extract_date(journal)
                my_dates.append(journal_date)
                if journal_date == report_date:
                    return self._extract_issue_data_safe(detailed)
            # 记录失败原因
            if my_count == 0:
                _diag(f"  FAIL #{issue.id}: journal 中无本人(user_id={user_id})记录, "
                      f"author_id={author_id}, status={status_name}, "
                      f"journal 总数={len(journals)}")
            else:
                _diag(f"  FAIL #{issue.id}: 本人有 {my_count} 条记录但日期不匹配, "
                      f"日期={sorted(set(my_dates))}, 目标={report_date}, "
                      f"author_id={author_id}, status={status_name}")
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

    def _extract_issue_data_safe(self, issue: Any) -> dict[str, Any] | None:
        """安全提取 Issue 数据，失败返回 None。"""
        try:
            return self._extract_issue_data(issue)
        except Exception:
            logger.warning("journal #%d: 提取数据失败", getattr(issue, "id", "?"))
            return None

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
        }

    @staticmethod
    def _to_beijing_date(ts: Any) -> str:
        """将时间戳转为北京时间日期字符串 YYYY-MM-DD。"""
        if not ts:
            return ""
        try:
            from datetime import timedelta, timezone
            tz_cn = timezone(timedelta(hours=8))
            if isinstance(ts, datetime):
                dt = ts
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(tz_cn).strftime("%Y-%m-%d")
            s = str(ts)
            if "T" in s:
                s_clean = s.replace("Z", "+00:00")
                return datetime.fromisoformat(s_clean).astimezone(tz_cn).strftime("%Y-%m-%d")
            return s[:10]
        except (ValueError, IndexError):
            return str(ts)[:10]

    @staticmethod
    def _extract_date(journal: Any) -> str:
        """从 journal 的 created_on 提取北京时间日期。"""
        return RedmineClient._to_beijing_date(
            getattr(journal, "created_on", None)
        )

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
                    author_id=raw.get("author_id", 0),
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
            current_user_id=user_id,
        )
