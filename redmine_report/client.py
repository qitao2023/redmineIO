"""Redmine API 封装 — 所有 API 调用的唯一入口。"""

import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone as _tz
from typing import Any

import requests as _requests
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
        self._last_timing: dict = {}

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

    def _get_user_activity_issues(self, user_id: int, date_str: str) -> tuple[set[int], str]:
        """通过 Redmine 活动 API 快速定位用户当天参与过的 Issue ID。

        比起逐个拉 journal，一次请求即可筛掉无关 Issue。
        返回 (issue_ids, error_msg)。
        """
        url = f"{self.url}/activity.json"
        try:
            resp = _requests.get(
                url,
                params={
                    "user_id": user_id,
                    "from": date_str,
                    "to": date_str,
                    "limit": 500,
                },
                headers={
                    "X-Redmine-API-Key": self._api_key,
                    "Accept": "application/json",
                },
                timeout=30,
            )
            status = resp.status_code
            if status != 200:
                return set(), f"HTTP {status}"
            data = resp.json()
            events = data.get("events", [])
            issue_ids: set[int] = set()
            for event in events:
                etype = event.get("event_type", "")
                if etype in ("issue", "issue-closed", "issue-edit", "issue-note"):
                    try:
                        url_parts = event.get("url", "").rstrip("/").split("/")
                        iid = int(url_parts[-1])
                        issue_ids.add(iid)
                    except (ValueError, IndexError):
                        pass
            logger.info(
                "活动 API 返回 %s 条事件，命中 %s 个 Issue",
                len(events), len(issue_ids),
            )
            return issue_ids, ""
        except Exception as e:
            return set(), str(e)[:100]

    def _get_user_project_ids(self, user_id: int) -> list[int]:
        """获取用户实际参与的项目 ID 列表。

        优先通过 membership API 获取用户真实所属项目，
        失败时回退到 list_projects()（所有可访问项目）。
        """
        t0 = time.perf_counter()
        try:
            user = self._redmine.user.get(user_id, include="memberships")
            memberships = getattr(user, "memberships", []) or []
            project_ids: list[int] = []
            for m in memberships:
                proj = getattr(m, "project", None)
                if proj is not None:
                    pid = getattr(proj, "id", None)
                    if pid is not None:
                        project_ids.append(int(pid))
            elapsed = time.perf_counter() - t0
            if project_ids:
                logger.info(
                    "用户 %s 的 membership 项目: %s", user_id, project_ids
                )
                print(f"  [计时] 获取用户项目 (membership API): {elapsed:.2f}s，{len(project_ids)} 个项目", file=sys.stderr)
                return project_ids
        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.warning("membership API 失败，回退到 list_projects: %s", str(e)[:100])
            print(f"  [计时] membership API 失败 ({elapsed:.2f}s)，回退到 list_projects", file=sys.stderr)

        # fallback: 所有可访问项目
        t0 = time.perf_counter()
        projects = self.list_projects()
        fallback_ids = [p["id"] for p in projects]
        elapsed = time.perf_counter() - t0
        logger.info("回退使用 list_projects: %s 个项目", len(fallback_ids))
        print(f"  [计时] 获取用户项目 (list_projects 兜底): {elapsed:.2f}s，{len(fallback_ids)} 个项目", file=sys.stderr)
        return fallback_ids

    def get_issues_by_date(
        self,
        report_date: str,
        user_id: int,
        project_ids: list[int] | None = None,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        """获取指定用户在指定日期处理过的 Issues。

        Args:
            report_date: 报告日期 YYYY-MM-DD。
            user_id: 当前用户 ID。
            project_ids: 限定查询的项目 ID 列表。为 None 时自动获取用户所属项目。
            limit: 单次查询返回上限（仅用于覆盖 Redmine 默认 25 条分页限制）。
        """
        # project_ids 为空时：方案3 回退到原来的全站查询（不额外调用 membership API）
        t_total_start = time.perf_counter()

        seen: set[int] = set()
        candidates: list = []
        seen_lock = threading.Lock()

        def _query_date(date_str, **extra_filters):
            """查询全站 Issue（无项目限定时使用）。"""
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

        # ═══ 阶段1: 并行 filter 查询 ═══
        t1 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=8) as executor:
            if project_ids:
                # 有项目限定：三种方案全部按项目拆分，project_id 作为 filter 参数
                futures = []
                for pid in project_ids:
                    futures.append(
                        executor.submit(_query_date, report_date, author_id=user_id, project_id=pid)
                    )
                    futures.append(
                        executor.submit(_query_date, report_date, assigned_to_id=user_id, project_id=pid)
                    )
                    futures.append(
                        executor.submit(_query_date, report_date, project_id=pid)
                    )
            else:
                # 未配置项目：回退原逻辑（全站查询）
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
        t2 = time.perf_counter()
        filter_elapsed = t2 - t1
        query_count = len(futures)
        print(
            f"  [计时] 阶段1-filter查询: {filter_elapsed:.2f}s，"
            f"共 {query_count} 次查询，候选 {len(candidates)} 个 Issue",
            file=sys.stderr,
        )

        # ── 收集候选 Issue 详情（供调试召回）──
        candidates_detail: list[str] = []
        for issue in candidates:
            proj = getattr(issue.project, "name", "?") if hasattr(issue, "project") else "?"
            candidates_detail.append(f"#{issue.id} [{proj}] {issue.subject or ''}")

        # ═══ 阶段2: 预分类（新增检测） ═══
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

        t3 = time.perf_counter()
        print(
            f"  [计时] 阶段2-预分类: {t3 - t2:.2f}s，"
            f"新增 {len(result)} 个 + 待验证 {len(need_journal_check)} 个",
            file=sys.stderr,
        )

        # ═══ 阶段3: journal 验证 ═══
        result_before_journal = len(result)
        self._check_journals_concurrent(
            need_journal_check, user_id, report_date, verified, result
        )
        t4 = time.perf_counter()
        journal_elapsed = t4 - t3
        confirmed_by_journal = len(result) - result_before_journal
        print(
            f"  [计时] 阶段3-journal验证: {journal_elapsed:.2f}s，"
            f"检查 {len(need_journal_check)} 个 → 确认 {confirmed_by_journal} 个",
            file=sys.stderr,
        )

        result.sort(key=lambda x: x.get("updated_on", ""), reverse=True)

        t_total = time.perf_counter() - t_total_start
        timing = {
            "filter": filter_elapsed,
            "filter_queries": query_count,
            "filter_candidates": len(candidates),
            "candidates_detail": candidates_detail,
            "preclassify": round(t3 - t2, 2),
            "preclassify_new": len(result) - confirmed_by_journal,
            "preclassify_pending": len(need_journal_check),
            "journal": journal_elapsed,
            "journal_checked": len(need_journal_check),
            "journal_confirmed": confirmed_by_journal,
            "total": round(t_total, 2),
        }
        self._last_timing = timing
        print(
            f"  [计时] get_issues_by_date 总计: {t_total:.2f}s "
            f"(filter={filter_elapsed:.2f} + 预分类={t3 - t2:.2f} "
            f"+ 活动API={t_act_elapsed:.2f} + journal={journal_elapsed:.2f})",
            file=sys.stderr,
        )
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
        if not issues:
            return

        t0 = time.perf_counter()
        total_fetched = 0
        total_failed = 0
        count_lock = threading.Lock()

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
            """检查 journals，找用户当天最后一次改状态的记录。"""
            nonlocal total_fetched, total_failed
            result_item = _fetch_detailed(issue.id)
            if result_item is None:
                with count_lock:
                    total_failed += 1
                return None
            with count_lock:
                total_fetched += 1
            detailed, journals = result_item

            # 倒序找用户当天最后一条 journal
            status_override = ""
            found = False
            for journal in reversed(journals):
                try:
                    ju = getattr(journal, "user", None)
                    jid = getattr(ju, "id", None) if ju else None
                except Exception:
                    continue
                if str(jid) != str(user_id):
                    continue
                jd = self._to_beijing_date(getattr(journal, "created_on", None))
                if jd != report_date:
                    continue
                found = True

                # 直接从原始 JSON 提取 status 变更，绕过 redminelib 封装
                raw = getattr(journal, "_data", {})
                for d in raw.get("details", []):
                    if isinstance(d, dict):
                        if d.get("name") == "status_id":
                            val = d.get("new_value", "")
                            if val:
                                status_override = str(val)
                                break
                break

            if not found:
                return None

            return self._extract_issue_data(detailed, status_override=status_override)

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

        elapsed = time.perf_counter() - t0
        avg_per = elapsed / len(issues) if issues else 0
        print(
            f"    [计时] journal内部: {elapsed:.2f}s（{len(issues)}个，"
            f"成功{total_fetched}/失败{total_failed}，均{avg_per:.2f}s/个）",
            file=sys.stderr,
        )

    def _extract_issue_data(self, issue: Any, status_override: str = "") -> dict[str, Any]:
        """从 Issue 资源提取结构化数据。

        status_override: 当事人在 journal 中最后设置的状态，
                         非空时替代 issue 当前状态。
        """
        updated = getattr(issue, "updated_on", "")
        time_str = self._extract_time(updated)
        status = status_override if status_override else getattr(issue.status, "name", "")

        return {
            "issue_id": issue.id,
            "subject": issue.subject or "",
            "project_name": getattr(issue.project, "name", ""),
            "tracker_name": getattr(issue.tracker, "name", ""),
            "status_name": status,
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

    def build_report_data(
        self,
        report_date: str | None = None,
        project_ids: list[int] | None = None,
    ) -> DailyReport:
        """编排所有 API 调用，构建完整的 DailyReport 对象。

        Args:
            report_date: 报告日期 YYYY-MM-DD，None=今天。
            project_ids: 限定查询的项目 ID 列表，None=自动获取。
        """
        t_build_start = time.perf_counter()
        if report_date is None:
            report_date = date.today().isoformat()

        t_auth_start = time.perf_counter()
        user_info = self.authenticate()
        user_id = user_info["id"]
        auth_elapsed = time.perf_counter() - t_auth_start

        raw_issues = self.get_issues_by_date(report_date, user_id, project_ids=project_ids)
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

        timing = dict(self._last_timing)
        timing["auth"] = round(auth_elapsed, 2)
        timing["build_total"] = round(time.perf_counter() - t_build_start, 2)

        return DailyReport(
            user_name=user_info["name"],
            date=report_date,
            weekday_cn=weekday_cn,
            entries=entries,
            total_issues=len(entries),
            project_count=len(unique_projects),
            current_user_id=user_id,
            report_date=report_date,
            timing=timing,
        )
