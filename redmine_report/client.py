"""Redmine API 封装 — 所有 API 调用的唯一入口。"""

import logging
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone as _tz
from typing import Any

import requests as _requests
from redminelib import Redmine
from redminelib.engines import DefaultEngine
from redminelib.exceptions import (
    AuthError,
    BaseRedmineError,
)

from .models import CancelledError, DailyReport, IssueEntryData

logger = logging.getLogger(__name__)

# 中文周几映射
_WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 预计算北京时间时区，避免每次调用时重复创建
_BEIJING_TZ = _tz(timedelta(hours=8))

# 并发参数
_FILTER_WORKERS = 12         # 阶段1: filter 查询并发数
_NEW_ISSUE_WORKERS = 20      # 阶段2: 新增 Issue 详情抓取并发数
_JOURNAL_WORKERS = 32        # 阶段3: journal 抓取并发数

# 分页参数。Redmine 单页上限是 100，请求 limit=300 时 python-redmine
# 会固定发 3 次请求（300/200/100），即使结果只有几条。这里自己翻页，
# 遇到不满一页就停止，少量数据时只发 1 次请求。
_PAGE_SIZE = 100
_MAX_RESULTS_PER_QUERY = 300

# 同一进程内复用 Issue 详情（含 journals）。key 中包含 updated_on：
# 只要 Issue 没被修改，重复生成日报时就不需要再逐个拉 journal。
_DETAIL_CACHE_MAX = 400
_DETAIL_CACHE_TTL = 30 * 60
_DETAIL_CACHE: "OrderedDict[tuple, tuple[float, Any]]" = OrderedDict()
_DETAIL_CACHE_LOCK = threading.Lock()

# 认证结果短期缓存（同一 Key 在多次生成之间复用）
_AUTH_CACHE_TTL = 10 * 60
_AUTH_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_AUTH_CACHE_LOCK = threading.Lock()

# 连接池大小：与 journal 并发数匹配，避免每个线程重新建 TCP 连接
_POOL_SIZE = 32

# 跨实例共享的状态映射缓存（GUI 每次生成都新建 RedmineClient，缓存避免重复拉取）
_STATUS_MAP_CACHE: dict[tuple[str, str], dict[str, str]] = {}
_STATUS_MAP_CACHE_LOCK = threading.Lock()

class _TimeoutSession(_requests.Session):
    """给每个请求补上默认 timeout 的 Session。

    python-redmine 2.5 会把 ``requests={'timeout': N}`` 挂到 Session 对象上，
    但 ``Session.request()`` 并不会读取这个属性。结果是连接一旦卡住，只能
    等操作系统层面的超时（常见 20-30 秒），整个日报生成都会被拖住。
    """

    def __init__(self, timeout: float = 15):
        super().__init__()
        self.default_timeout = timeout
        adapter = _requests.adapters.HTTPAdapter(
            pool_connections=_POOL_SIZE,
            pool_maxsize=_POOL_SIZE,
            pool_block=False,
        )
        self.mount("http://", adapter)
        self.mount("https://", adapter)

    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", self.default_timeout)
        return super().request(*args, **kwargs)


class _TimeoutEngine(DefaultEngine):
    """DefaultEngine + 真正生效的默认 timeout。"""

    @staticmethod
    def create_session(**params):
        timeout = params.pop("timeout", None)
        session = _TimeoutSession(timeout if timeout is not None else 15)
        for key, value in params.items():
            setattr(session, key, value)
        return session


def _detail_cache_get(key: tuple) -> Any:
    now = time.time()
    with _DETAIL_CACHE_LOCK:
        item = _DETAIL_CACHE.get(key)
        if item is None:
            return None
        created, value = item
        if now - created > _DETAIL_CACHE_TTL:
            _DETAIL_CACHE.pop(key, None)
            return None
        _DETAIL_CACHE.move_to_end(key)
        return value


def _detail_cache_put(key: tuple, value: Any) -> None:
    with _DETAIL_CACHE_LOCK:
        _DETAIL_CACHE[key] = (time.time(), value)
        _DETAIL_CACHE.move_to_end(key)
        while len(_DETAIL_CACHE) > _DETAIL_CACHE_MAX:
            _DETAIL_CACHE.popitem(last=False)


def _related_id(resource: Any, attr: str) -> int:
    """安全读取关联对象的 id。

    redminelib 在关联字段为 JSON null（例如未指派 assigned_to）时，
    直接 getattr 会抛 TypeError；这里先看原始 dict，避免触发该问题。
    """
    data = getattr(resource, "_decoded_attrs", None)
    if isinstance(data, dict) and attr in data:
        value = data.get(attr)
        if value is None:
            return 0
        if isinstance(value, dict):
            try:
                return int(value.get("id") or 0)
            except (TypeError, ValueError):
                return 0
    try:
        value = getattr(resource, attr, None)
        if value is None:
            return 0
        return int(getattr(value, "id", 0) or 0)
    except Exception:
        return 0


def _updated_on_filter(report_date: str, today: str | None = None) -> str:
    """构造 Redmine 的 updated_on 查询条件。

    当天日报用单日条件；历史日报必须从报告日一直查到今天，因为：
    Redmine 的 updated_on 是“最后一次更新时间”。某个 Issue 在报告日
    被本人操作过、之后又被别人更新，它的 updated_on 已经跑到后面去了，
    单日查询会直接漏掉它（后面的 journal 校验再准也没机会看到）。

    返回单日 ``YYYY-MM-DD`` 或 Redmine 的区间写法
    ``><YYYY-MM-DD|YYYY-MM-DD``，由 journal 校验再按报告日过滤。
    """
    if today is None:
        today = datetime.now(_BEIJING_TZ).date().isoformat()
    try:
        datetime.strptime(report_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return report_date
    if report_date < today:
        return f"><{report_date}|{today}"
    return report_date


class RedmineClientError(Exception):
    """Redmine 客户端错误。"""
    pass


class RedmineClient:
    """封装 Redmine REST API 调用。"""

    def __init__(self, url: str, api_key: str, timeout: int = 15):
        self.url = url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        # python-redmine 的 ResourceManager 把 url/params 存在实例属性上，
        # 多线程共享同一个 Redmine 对象会互相覆盖请求参数。这里每个线程
        # 使用独立的 Redmine 实例（各自持有连接池），请求互不干扰。
        self._thread_local = threading.local()
        self._last_timing: dict = {}
        self._status_map: dict[str, str] | None = None  # status_id → status_name
        self._status_map_lock = threading.Lock()  # 状态映射懒加载的并发保护

        try:
            self._api()
        except BaseRedmineError as e:
            raise RedmineClientError(
                f"无法连接到 Redmine 服务器: {self.url}\n{str(e)[:200]}"
            ) from e

    def _api(self) -> Redmine:
        """返回当前线程专属的 Redmine 客户端。"""
        api = getattr(self._thread_local, "redmine", None)
        if api is None:
            api = Redmine(
                self.url,
                key=self._api_key,
                engine=_TimeoutEngine,
                requests={"timeout": self._timeout},
            )
            self._thread_local.redmine = api
        return api

    def authenticate(self) -> dict[str, Any]:
        """验证 API Key 有效性，返回当前用户信息。"""
        cache_key = (self.url, self._api_key)
        with _AUTH_CACHE_LOCK:
            cached = _AUTH_CACHE.get(cache_key)
            if cached is not None and time.time() - cached[0] <= _AUTH_CACHE_TTL:
                return dict(cached[1])

        try:
            user = self._api().user.get("current")

            name = ""
            lastname = getattr(user, "lastname", "")
            firstname = getattr(user, "firstname", "")
            if lastname or firstname:
                name = f"{lastname}{firstname}"
            if not name:
                name = getattr(user, "name", "") or getattr(user, "login", "")

            info = {
                "id": user.id,
                "login": getattr(user, "login", ""),
                "name": name,
                "mail": getattr(user, "mail", ""),
            }
            with _AUTH_CACHE_LOCK:
                _AUTH_CACHE[cache_key] = (time.time(), dict(info))
            return info
        except AuthError:
            raise RedmineClientError(
                "API Key 验证失败，请检查 Key 是否正确。\n"
                "获取方式：Redmine → 我的账号 → API 访问键 → 显示。"
            )
        except BaseRedmineError as e:
            raise RedmineClientError(f"认证过程出错: {str(e)[:200]}") from e
        except _requests.exceptions.RequestException as e:
            raise RedmineClientError(
                f"连接 Redmine 超时或网络错误: {str(e)[:200]}"
            ) from e

    def _ensure_status_map(self) -> None:
        """懒加载 status_id → status_name 映射（线程安全，跨实例缓存）。"""
        if self._status_map is not None:
            return
        cache_key = (self.url, self._api_key)
        with _STATUS_MAP_CACHE_LOCK:
            cached = _STATUS_MAP_CACHE.get(cache_key)
        if cached is not None:
            self._status_map = dict(cached)
            return
        with self._status_map_lock:
            if self._status_map is not None:
                return
            # 进入锁后再查一次全局缓存：避免并发线程看到一个空的
            # self._status_map 后误判为“已加载”，进而逐条查询状态。
            with _STATUS_MAP_CACHE_LOCK:
                cached = _STATUS_MAP_CACHE.get(cache_key)
            if cached is not None:
                self._status_map = dict(cached)
                return
            status_map: dict[str, str] = {}
            try:
                for s in self._api().issue_status.all():
                    status_map[str(s.id)] = s.name
                logger.info("状态映射已加载: %d 个状态", len(status_map))
                with _STATUS_MAP_CACHE_LOCK:
                    _STATUS_MAP_CACHE[cache_key] = dict(status_map)
            except Exception as e:
                logger.warning("批量加载状态映射失败: %s，将按需单条查询", str(e)[:120])
            self._status_map = status_map

    def _lookup_status(self, status_id: str) -> str:
        """按 status_id 查名称，缓存未命中时按需单条查询。"""
        self._ensure_status_map()
        if status_id in self._status_map:
            return self._status_map[status_id]
        try:
            s = self._api().issue_status.get(int(status_id))
            self._status_map[status_id] = s.name
            logger.info("按需查询状态 id=%s → %s", status_id, s.name)
            return s.name
        except Exception:
            logger.warning("按需查询状态 id=%s 失败", status_id)
            return ""

    def _get_effective_status(self, detailed_issue: Any, user_id: int,
                              report_date: str) -> str:
        """确定当事人在报告日期最后一次接触 Issue 时的有效状态。

        规则：
        1. 当事人亲自改了状态 → 用 new_value
        2. 当事人没改状态 → 往前追溯最近的状态变更；
           往前没找到 → 往后找第一条状态变更的 old_value
        3. 无当事人 journal + 本人今日创建 → 从历史记录反推初始状态
        4. 其他 → 回退当前状态
        """
        journals = list(getattr(detailed_issue, "journals", []))
        self._ensure_status_map()

        # 检查是否本人今日创建
        author_id = _related_id(detailed_issue, "author")
        created_on = self._to_beijing_date(getattr(detailed_issue, "created_on", None))
        is_new_today = (int(author_id) == int(user_id) and created_on == report_date)

        # 找到当事人在报告日期的最后一次 journal 及其状态变更
        user_last_idx = -1
        user_status_new = None

        for i, journal in enumerate(journals):
            ju = getattr(journal, "user", None)
            jid = getattr(ju, "id", None) if ju else None
            if str(jid) != str(user_id):
                continue
            jd = self._to_beijing_date(getattr(journal, "created_on", None))
            if jd != report_date:
                continue
            user_last_idx = i
            for d in getattr(journal, "details", []):
                dd = dict(d) if hasattr(d, '__iter__') else {}
                if str(dd.get("name", "")) == "status_id":
                    user_status_new = str(dd.get("new_value", ""))

        if user_last_idx < 0:
            if is_new_today:
                # 本人今日创建但无 journal，从历史记录反推初始状态
                return self._get_initial_status(detailed_issue)
            return getattr(detailed_issue.status, "name", "")

        if user_status_new:
            return self._lookup_status(user_status_new) or getattr(
                detailed_issue.status, "name", "")

        # 当事人没改状态：
        # 1) 往前追溯 — 找当事人 journal 之前的最后一条状态变更
        # 2) 还没找到 → 往后追溯 — 第一条状态变更的 old_value 即当时状态
        for i in range(user_last_idx - 1, -1, -1):
            for d in getattr(journals[i], "details", []):
                dd = dict(d) if hasattr(d, '__iter__') else {}
                if str(dd.get("name", "")) == "status_id":
                    sid = str(dd.get("new_value", ""))
                    name = self._lookup_status(sid)
                    if name:
                        return name
                    continue  # 状态不在map中，继续往前找

        for i in range(user_last_idx + 1, len(journals)):
            for d in getattr(journals[i], "details", []):
                dd = dict(d) if hasattr(d, '__iter__') else {}
                if str(dd.get("name", "")) == "status_id":
                    old_id = str(dd.get("old_value", ""))
                    if old_id:
                        name = self._lookup_status(old_id)
                        if name:
                            return name
                        continue  # 状态不在map中，继续往后找

        # 完全没有状态变更记录，用当前状态（即初始状态）
        return getattr(detailed_issue.status, "name", "")

    def _get_initial_status(self, detailed_issue: Any) -> str:
        """从历史记录反推 Issue 创建时的初始状态。

        找第一条 status_id 变更的 old_value = 初始状态。
        如果从未有过状态变更，当前状态即为初始状态。
        """
        journals = list(getattr(detailed_issue, "journals", []))
        self._ensure_status_map()

        for journal in journals:
            for d in getattr(journal, "details", []):
                dd = dict(d) if hasattr(d, '__iter__') else {}
                if str(dd.get("name", "")) == "status_id":
                    old_id = str(dd.get("old_value", ""))
                    if old_id:
                        name = self._lookup_status(old_id)
                        if name:
                            return name
                        continue

        return getattr(detailed_issue.status, "name", "")

    def list_projects(self) -> list[dict[str, Any]]:
        """列出当前用户可访问的所有项目。"""
        try:
            projects = []
            for p in self._api().project.all():
                projects.append({"id": p.id, "name": p.name, "identifier": p.identifier})
            return projects
        except BaseRedmineError as e:
            raise RedmineClientError(f"获取项目列表失败: {str(e)[:200]}") from e
        except _requests.exceptions.RequestException as e:
            raise RedmineClientError(
                f"获取项目列表超时或网络错误: {str(e)[:200]}"
            ) from e

    def _get_user_project_ids(self, user_id: int) -> list[int]:
        """获取用户实际参与的项目 ID 列表。

        优先通过 membership API 获取用户真实所属项目，
        失败时回退到 list_projects()（所有可访问项目）。
        """
        t0 = time.perf_counter()
        try:
            user = self._api().user.get(user_id, include="memberships")
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
        user_name: str = "",
        skip_review: bool = False,
        review_strict: bool = False,
        support_always_new: bool = False,
        progress_callback: "callable | None" = None,
        cancel_check: "callable | None" = None,
        limit: int = _PAGE_SIZE,
        debug_journals: bool = False,
    ) -> list[dict[str, Any]]:
        """获取指定用户在指定日期处理过的 Issues。

        Args:
            report_date: 报告日期 YYYY-MM-DD。
            user_id: 当前用户 ID。
            project_ids: 限定查询的项目 ID 列表。为 None 时自动获取用户所属项目。
            user_name: 当前用户名（保留参数兼容，当前不再用于预筛）。
            skip_review: True=跳过方案3（不查审核复核），大幅提速。
            review_strict: True=审核复核仅计入状态/指派变更，纯评论不算。
            limit: 单页查询上限（Redmine 上限 100；内部会自动翻页，
                   最多取 _MAX_RESULTS_PER_QUERY 条）。
            debug_journals: True=输出前 3 条已确认 Issue 的 journal 原文用于调试
                            （复用已抓取的详情，通常不额外发请求）。
        """
        # project_ids 为空时：方案3 回退到原来的全站查询（不额外调用 membership API）
        def _p(msg, pct):
            if progress_callback:
                progress_callback(msg, pct)

        def _should_cancel():
            return cancel_check is not None and cancel_check()

        t_total_start = time.perf_counter()

        seen: set[int] = set()
        candidates: list = []
        seen_lock = threading.Lock()
        query_failures = 0  # 阶段1: 整批 filter 查询失败次数（该查询的 Issue 可能全部缺失）

        project_id_set: set[int] = set()
        if project_ids:
            for pid in project_ids:
                try:
                    project_id_set.add(int(pid))
                except (TypeError, ValueError):
                    pass

        page_size = max(1, min(int(limit or _PAGE_SIZE), _PAGE_SIZE))
        updated_filter = _updated_on_filter(report_date)
        historical = updated_filter != report_date

        def _query_date(updated_on_filter: str,
                        apply_project_filter: bool = False,
                        **extra_filters):
            """分页查询 Issue，遇到不满一页立即停止。

            updated_on_filter 既可以是单日 YYYY-MM-DD，也可以是
            Redmine 区间写法 ><from|to（历史日报使用）。
            apply_project_filter=True 时只保留选中项目（用于全站
            author/assigned 安全网查询），避免把未勾选项目带进来。
            """
            nonlocal query_failures
            try:
                offset = 0
                while offset < _MAX_RESULTS_PER_QUERY:
                    result_set = self._api().issue.filter(
                        updated_on=updated_on_filter,
                        status_id="*",
                        sort="updated_on:desc",
                        limit=page_size,
                        offset=offset,
                        **extra_filters,
                    )
                    page = list(result_set)
                    if not page:
                        break
                    for issue in page:
                        if apply_project_filter and project_id_set:
                            pid = _related_id(issue, "project")
                            if pid and pid not in project_id_set:
                                continue
                        with seen_lock:
                            if issue.id not in seen:
                                seen.add(issue.id)
                                candidates.append(issue)
                    offset += len(page)
                    try:
                        total = result_set.total_count
                    except Exception:
                        total = None
                    if total is not None and offset >= int(total):
                        break
                    if len(page) < page_size:
                        break
            except Exception as e:
                with seen_lock:
                    query_failures += 1
                logger.warning(
                    "查询 updated_on=%s 失败: %s",
                    updated_on_filter, str(e)[:100],
                )

        # ═══ 阶段1: 并行 filter 查询 ═══
        t1 = time.perf_counter()

        with ThreadPoolExecutor(max_workers=_FILTER_WORKERS) as executor:
            if project_ids:
                if skip_review:
                    # 只查本人：仍按项目查询，保留 Redmine 对子项目的
                    # 原有包含行为（N 个项目 = 2N 次请求）。
                    futures = []
                    for pid in project_ids:
                        futures.append(executor.submit(
                            _query_date, updated_filter, False,
                            author_id=user_id, project_id=pid,
                        ))
                        futures.append(executor.submit(
                            _query_date, updated_filter, False,
                            assigned_to_id=user_id, project_id=pid,
                        ))
                else:
                    # 本人创建/经办的全站查询作为安全网（2 次请求），
                    # 每个项目只再查一次“当天所有更新”，不再按
                    # author/assigned/全部 各查一遍（原来 3N 次，现在 N+2 次）。
                    futures = [
                        executor.submit(
                            _query_date, updated_filter, True,
                            author_id=user_id,
                        ),
                        executor.submit(
                            _query_date, updated_filter, True,
                            assigned_to_id=user_id,
                        ),
                    ]
                    for pid in project_ids:
                        futures.append(
                            executor.submit(
                                _query_date, updated_filter, False,
                                project_id=pid,
                            )
                        )
            else:
                # 未配置项目：回退原逻辑（全站查询）
                futures = [
                    executor.submit(
                        _query_date, updated_filter, False, author_id=user_id
                    ),
                    executor.submit(
                        _query_date, updated_filter, False,
                        assigned_to_id=user_id,
                    ),
                    executor.submit(_query_date, updated_filter),
                ]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    query_failures += 1
                    logger.warning("filter 查询发生未预期错误: %s", str(e)[:100])
        t2 = time.perf_counter()
        filter_elapsed = t2 - t1
        query_count = len(futures)
        print(
            f"  [计时] 阶段1-filter查询(updated_on={updated_filter}): "
            f"{filter_elapsed:.2f}s，"
            f"共 {query_count} 次查询，候选 {len(candidates)} 个 Issue",
            file=sys.stderr,
        )
        _p(f"filter查询完成，候选{len(candidates)}个", 25)

        if _should_cancel():
            raise CancelledError("用户取消")

        # ── 收集候选 Issue 详情（供调试召回）──
        candidates_detail: list[str] = []
        for issue in candidates:
            proj = getattr(issue.project, "name", "?") if hasattr(issue, "project") else "?"
            candidates_detail.append(f"#{issue.id} [{proj}] {issue.subject or ''}")

        # ═══ 阶段2: 预分类（新增检测） ═══
        result: list[dict[str, Any]] = []
        verified: set[int] = set()
        need_journal_check: list = []
        new_issues: list = []

        for issue in candidates:
            author_id = _related_id(issue, "author")
            created_on = self._to_beijing_date(getattr(issue, "created_on", None))
            # 新增 — 本人创建 + 当天创建；其余 Issue 需要 journal 验证
            if author_id == user_id and created_on == report_date:
                new_issues.append(issue)
            else:
                need_journal_check.append(issue)

        # 搜索 API 预筛已停用：它按文本命中情况丢弃候选，会漏掉
        # 审核/复核记录。journal 才是“本人当天是否真的操作过”的
        # 权威判据，不能为了少几次请求而牺牲完整性。

        # 新增 Issue 的详情抓取是相互独立的 I/O，并行化（判定逻辑不变）。
        # "支持"类：不限状态，始终取创建时的初始状态；其他类：取当事人最后一次操作时的有效状态。
        new_detail_failures = 0  # 阶段2: 新增 Issue 详情抓取失败次数
        new_detail_lock = threading.Lock()

        def _compute_new_status(issue):
            """抓取新增 Issue 的 journals 并计算其有效状态。"""
            nonlocal new_detail_failures
            tracker_name = getattr(issue.tracker, "name", "")
            status_override = ""
            time_override = ""
            try:
                detailed = self._get_detailed(
                    issue.id, getattr(issue, "updated_on", None)
                )
            except Exception:
                with new_detail_lock:
                    new_detail_failures += 1
                return issue, "", ""
            try:
                if support_always_new and tracker_name and "支持" in tracker_name:
                    status_override = self._get_initial_status(detailed)
                else:
                    status_override = self._get_effective_status(detailed, user_id, report_date)
            except Exception:
                pass
            if historical:
                # 新增 Issue 也优先用本人报告日的最后操作时间；
                # 没有 journal 时退回创建时间，避免被之后的更新打乱顺序。
                time_override = self._latest_user_journal_time(
                    detailed, user_id, report_date
                )
                if not time_override:
                    time_override = self._extract_time(
                        getattr(issue, "created_on", None)
                    )
            return issue, status_override, time_override

        if new_issues:
            with ThreadPoolExecutor(max_workers=_NEW_ISSUE_WORKERS) as executor:
                futures = [executor.submit(_compute_new_status, iss) for iss in new_issues]
                for future in as_completed(futures):
                    try:
                        issue, status_override, time_override = future.result()
                    except Exception:
                        continue
                    data = self._extract_issue_data(
                        issue,
                        status_override=status_override,
                        time_override=time_override or None,
                    )
                    if data["issue_id"] not in verified:
                        verified.add(data["issue_id"])
                        result.append(data)

        t3 = time.perf_counter()
        print(
            f"  [计时] 阶段2-预分类: {t3 - t2:.2f}s，"
            f"新增 {len(result)} 个 + 待验证 {len(need_journal_check)} 个",
            file=sys.stderr,
        )
        _p(f"预分类完成，待验证{len(need_journal_check)}个", 40)

        if _should_cancel():
            raise CancelledError("用户取消")

        # ═══ 阶段2.5: 搜索预筛已停用（保留计时字段兼容旧界面） ═══
        t_act_start = time.perf_counter()
        search_issues: set[int] = set()
        search_error = ""
        search_debug = ""
        skipped_by_search = 0
        t_act_elapsed = time.perf_counter() - t_act_start
        print(
            f"  [计时] 搜索预筛: 已停用（以 journal 为准）",
            file=sys.stderr,
        )

        # ═══ 阶段3: journal 验证 ═══
        if _should_cancel():
            raise CancelledError("用户取消")

        result_before_journal = len(result)
        _p(f"正在验证journal（共{len(need_journal_check)}个）...", 55)
        rejected, journal_failures = self._check_journals_concurrent(
            need_journal_check, user_id, report_date, verified, result,
            review_strict=review_strict,
            historical=historical,
            cancel_check=cancel_check,
        )
        t4 = time.perf_counter()
        journal_elapsed = t4 - t3
        confirmed_by_journal = len(result) - result_before_journal
        _p(f"journal验证完成，确认{confirmed_by_journal}个", 80)
        print(
            f"  [计时] 阶段3-journal验证: {journal_elapsed:.2f}s，"
            f"检查 {len(need_journal_check)} 个 → 确认 {confirmed_by_journal} 个",
            file=sys.stderr,
        )

        result.sort(
            key=lambda x: (x.get("updated_on", ""), x.get("issue_id", 0)),
            reverse=True,
        )

        # ── 调试：抓已确认 Issue 的 journal 原始内容（仅 debug_journals=True 时）──
        journal_debug: list[str] = []
        for entry in (result[:3] if debug_journals else []):
            try:
                detail = self._get_detailed(
                    entry["issue_id"], entry.get("updated_on")
                )
                journal_debug.append(f"--- Issue #{entry['issue_id']} journals ---")
                for j in reversed(list(getattr(detail, "journals", []))):
                    ju = getattr(j, "user", None)
                    uname = getattr(ju, "name", "?") if ju else "?"
                    jtime_raw = getattr(j, "created_on", None)
                    jtime = self._to_beijing_date(jtime_raw) + " " + self._extract_time(jtime_raw) if jtime_raw else "?"
                    notes = getattr(j, "notes", "") or ""
                    journal_debug.append(f"  [{jtime}] user={uname} notes={notes[:150]}")
                    # 尝试多种方式获取 details
                    details = []
                    raw = getattr(j, "_data", {})
                    if isinstance(raw, dict) and not raw.get("details"):
                        # 打印 _data 的所有 key 帮助调试
                        journal_debug.append(f"    _data keys: {list(raw.keys())[:10]}")
                    details = raw.get("details", []) if isinstance(raw, dict) else []
                    # 方式2: redminelib details 属性
                    if not details:
                        try:
                            details = [dict(d) if hasattr(d, '__iter__') else str(d) for d in getattr(j, "details", [])]
                        except Exception:
                            pass
                    for d in details:
                        if isinstance(d, dict):
                            name = d.get("name", d.get("property", "?"))
                            journal_debug.append(
                                f"    detail: {name} "
                                f"old={d.get('old_value','?')} new={d.get('new_value','?')}"
                            )
                        else:
                            journal_debug.append(f"    detail(raw): {d}")
            except Exception as e:
                journal_debug.append(f"--- Issue #{entry['issue_id']} 拉取失败: {e} ---")
        journal_debug.append("")

        t_total = time.perf_counter() - t_total_start
        timing = {
            "filter": filter_elapsed,
            "updated_filter": updated_filter,
            "historical": historical,
            "filter_queries": query_count,
            "filter_candidates": len(candidates),
            "candidates_detail": candidates_detail,
            "preclassify": round(t3 - t2, 2),
            "preclassify_new": len(result) - confirmed_by_journal,
            "preclassify_pending": len(need_journal_check),
            "search_api": round(t_act_elapsed, 2),
            "search_hit": len(search_issues),
            "search_skipped": skipped_by_search,
            "search_error": search_error,
            "search_debug": search_debug,
            "journal": journal_elapsed,
            "journal_checked": len(need_journal_check),
            "journal_confirmed": confirmed_by_journal,
            "journal_rejected": (rejected or []) if review_strict else [],
            "total": round(t_total, 2),
            "journal_debug": journal_debug,
            "fetch_failures": {
                "filter": query_failures,
                "new_detail": new_detail_failures,
                "journal": journal_failures,
            },
        }
        self._last_timing = timing
        print(
            f"  [计时] get_issues_by_date 总计: {t_total:.2f}s "
            f"(filter={filter_elapsed:.2f} + 预分类={t3 - t2:.2f} "
            f"+ 搜索={t_act_elapsed:.2f} + journal={journal_elapsed:.2f})",
            file=sys.stderr,
        )
        return result

    def _get_detailed(self, issue_id: int, updated_on: Any) -> Any:
        """获取 Issue 详情（含 journals），带进程内缓存。

        缓存 key 里带上 updated_on：Issue 一旦被修改，updated_on 就会变，
        旧缓存自然失效；没变时重复生成日报无需再请求 journal。
        """
        updated_key = str(updated_on or "")
        key = (self.url, self._api_key, int(issue_id), updated_key)
        if updated_key:
            cached = _detail_cache_get(key)
            if cached is not None:
                return cached

        detailed = self._api().issue.get(issue_id, include="journals")
        if updated_key:
            _detail_cache_put(key, detailed)
        return detailed

    def _latest_user_journal_time(self, detailed: Any, user_id: int,
                                  report_date: str) -> str:
        """返回本人在报告日最后一次 journal 的 HH:MM。"""
        for journal in reversed(list(getattr(detailed, "journals", []))):
            if _related_id(journal, "user") != int(user_id):
                continue
            created_on = getattr(journal, "created_on", None)
            if self._to_beijing_date(created_on) != report_date:
                continue
            return self._extract_time(created_on)
        return ""

    def _check_journals_concurrent(
        self,
        issues: list,
        user_id: int,
        report_date: str,
        seen: set[int],
        result: list[dict[str, Any]],
        max_workers: int = _JOURNAL_WORKERS,
        review_strict: bool = False,
        historical: bool = False,
        cancel_check: "callable | None" = None,
    ):
        """并发检查 Issue 的 journal。

        review_strict=True 时：仅计入状态或指派变更，纯评论忽略。
        """
        if not issues:
            return [], 0

        t0 = time.perf_counter()
        total_fetched = 0
        total_failed = 0  # journal 抓取失败、被丢弃的 Issue 数
        rejected_debug: list[str] = []  # 被 review_strict 过滤的 Issue
        count_lock = threading.Lock()
        debug_lock = threading.Lock()

        def _should_cancel():
            return cancel_check is not None and cancel_check()

        def _fetch_detailed(issue_id: int, updated_on: Any) -> tuple[Any, list] | None:
            try:
                detailed = self._get_detailed(issue_id, updated_on)
                return detailed, list(getattr(detailed, "journals", []))
            except Exception:
                return None

        def _check_one(issue):
            """检查 journals，找用户当天最后一次改状态/指派的记录。"""
            nonlocal total_fetched, total_failed
            result_item = _fetch_detailed(
                issue.id, getattr(issue, "updated_on", None)
            )
            if result_item is None:
                with count_lock:
                    total_failed += 1
                return None
            with count_lock:
                total_fetched += 1
            detailed, journals = result_item

            # 倒序找用户当天所有 journal，汇总 status / assignee 变更
            has_status_change = False
            has_assignee_change = False
            found = False
            user_time = ""
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

                if not found:
                    found = True
                    user_time = self._extract_time(
                        getattr(journal, "created_on", None)
                    )

                # 汇总所有 journal 的 status / assignee 变更
                for d in getattr(journal, "details", []):
                    try:
                        dd = dict(d) if hasattr(d, '__iter__') else {}
                        name = str(dd.get("name", "") if dd else getattr(d, "name", ""))
                        if name == "status_id":
                            has_status_change = True
                        elif "assigned_to" in name:
                            has_assignee_change = True
                    except Exception:
                        continue

                # 已经找到了全部变更则提前退出
                if has_status_change and has_assignee_change:
                    break

            if not found:
                return None

            # review_strict 模式：状态和指派都没变 → 跳过
            if review_strict and not has_status_change and not has_assignee_change:
                # 记录被过滤的原因，方便排错
                all_names = []
                for j in journals:
                    for d in getattr(j, "details", []):
                        try:
                            dd = dict(d) if hasattr(d, '__iter__') else {}
                            if dd:
                                all_names.append(str(dd.get("name", "?")))
                            else:
                                all_names.append(str(getattr(d, "name", "?")))
                        except Exception:
                            pass
                with debug_lock:
                    rejected_debug.append(
                        f"#{issue.id} 过滤: 无status/assignee变更 "
                        f"(details字段: {', '.join(all_names[:10]) if all_names else '空'})"
                    )
                return None

            status_override = self._get_effective_status(detailed, user_id, report_date)
            return self._extract_issue_data(
                detailed,
                status_override=status_override,
                time_override=user_time if historical else None,
            )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_check_one, iss): iss for iss in issues}
            for future in as_completed(futures):
                if _should_cancel():
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise CancelledError("用户取消")
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
        return rejected_debug, total_failed

    def _extract_issue_data(self, issue: Any, status_override: str = "",
                            time_override: str | None = None) -> dict[str, Any]:
        """从 Issue 资源提取结构化数据。

        status_override: 当事人在 journal 中最后设置的状态，
                         非空时替代 issue 当前状态。
        time_override: 历史日报使用当事人在报告日的最后操作时间，
                       避免因为 Issue 之后又被更新而打乱日报顺序。
        """
        updated = getattr(issue, "updated_on", "")
        time_str = time_override if time_override else self._extract_time(updated)
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
            "author_id": _related_id(issue, "author"),
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
        skip_review: bool = False,
        review_strict: bool = False,
        support_always_new: bool = False,
        debug_journals: bool = False,
        progress_callback: "callable | None" = None,
        cancel_check: "callable | None" = None,
    ) -> DailyReport:
        """编排所有 API 调用，构建完整的 DailyReport 对象。

        Args:
            report_date: 报告日期 YYYY-MM-DD，None=今天。
            project_ids: 限定查询的项目 ID 列表，None=自动获取。
            skip_review: True=跳过方案3（审核复核），大幅提速。
            review_strict: True=审核复核仅计入状态/指派变更，纯评论不算。
            support_always_new: True=支持类Issue始终显示初始状态（新建）。
            debug_journals: True=额外抓取已确认 Issue 的 journal 原文用于调试。
            progress_callback: 进度回调 (msg, percent)。
        """
        t_build_start = time.perf_counter()
        if report_date is None:
            report_date = date.today().isoformat()

        def _p(msg, pct):
            if progress_callback:
                progress_callback(msg, pct)

        _p("正在认证...", 5)
        t_auth_start = time.perf_counter()
        user_info = self.authenticate()
        user_id = user_info["id"]
        auth_elapsed = time.perf_counter() - t_auth_start
        _p("已认证，正在查询Issue列表...", 10)

        # 状态映射与 Issue 列表查询互不依赖，提前并发加载。
        t_status_start = time.perf_counter()
        status_thread = threading.Thread(
            target=self._ensure_status_map, daemon=True
        )
        status_thread.start()

        raw_issues = self.get_issues_by_date(report_date, user_id,
                                              project_ids=project_ids,
                                              user_name=user_info["login"],
                                              skip_review=skip_review,
                                              review_strict=review_strict,
                                              support_always_new=support_always_new,
                                              debug_journals=debug_journals,
                                              progress_callback=progress_callback,
                                              cancel_check=cancel_check)
        status_thread.join(timeout=5)
        status_elapsed = time.perf_counter() - t_status_start
        _p(f"正在生成报告...({len(raw_issues)}个Issue)", 90)
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
        timing["status_map"] = round(status_elapsed, 2)
        timing["build_total"] = round(time.perf_counter() - t_build_start, 2)

        # 抓取失败警告：报告可能不完整（纯文本，避免 GBK 控制台打印崩溃）
        warnings: list[str] = []
        ff = timing.get("fetch_failures", {})
        if ff.get("filter"):
            warnings.append(
                f"有 {ff['filter']} 次项目查询失败，对应项目的 Issue 可能未收录"
            )
        if ff.get("journal"):
            warnings.append(
                f"有 {ff['journal']} 个 Issue 的日志抓取失败，对应记录可能缺失"
            )
        if ff.get("new_detail"):
            warnings.append(
                f"有 {ff['new_detail']} 个新增 Issue 详情抓取失败，状态可能不准确"
            )

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
            warnings=warnings,
        )
