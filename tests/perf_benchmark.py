"""Mock-Redmine benchmark for the daily report client.

This is a self-contained performance/regression harness.  It starts a fake
Redmine server, runs two report generations against it, and prints the elapsed
time plus the number of HTTP requests per endpoint.

Usage:
    python tests/perf_benchmark.py --package-root .
    python tests/perf_benchmark.py --package-root C:/tmp/redmineIO-baseline

The fake server deliberately serialises requests (one at a time) so request
count is directly visible in the elapsed time, similar to a small single-worker
Redmine installation.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


REPORT_DATE = "2026-10-17"

STATUSES = {
    1: "新建",
    2: "已解决",
    5: "已关闭",
}

USERS = {
    1: ("alice", "Alice"),
    2: ("bob", "Bob"),
    3: ("carol", "Carol"),
}

PROJECTS = {
    1: "项目一",
    2: "项目二",
    3: "项目三",
    4: "未勾选项目",
}
PROJECTS.update({i: f"空项目{i}" for i in range(5, 25)})


def _issue(
    issue_id: int,
    project_id: int,
    author_id: int,
    created_on: str,
    updated_on: str,
    tracker: str,
    status_id: int,
    assigned_to_id: int = 0,
    subject: str = "",
) -> dict:
    return {
        "id": issue_id,
        "project": {"id": project_id, "name": PROJECTS[project_id]},
        "tracker": {"id": 1, "name": tracker},
        "status": {"id": status_id, "name": STATUSES[status_id]},
        "priority": {"id": 3, "name": "普通"},
        "author": {"id": author_id, "name": USERS[author_id][1]},
        "assigned_to": (
            {"id": assigned_to_id, "name": USERS[assigned_to_id][1]}
            if assigned_to_id
            else None
        ),
        "subject": subject or f"测试问题 {issue_id}",
        "description": "",
        "created_on": created_on,
        "updated_on": updated_on,
    }


def _journal(
    journal_id: int,
    user_id: int,
    created_on: str,
    details: list[dict] | None = None,
    notes: str = "",
) -> dict:
    return {
        "id": journal_id,
        "user": {"id": user_id, "name": USERS[user_id][1]},
        "notes": notes,
        "created_on": created_on,
        "details": details or [],
    }


def _status_detail(old: int, new: int) -> dict:
    return {
        "property": "attr",
        "name": "status_id",
        "old_value": str(old),
        "new_value": str(new),
    }


def _assignee_detail(old: int, new: int) -> dict:
    return {
        "property": "attr",
        "name": "assigned_to_id",
        "old_value": str(old),
        "new_value": str(new),
    }


def build_fixture() -> tuple[list[dict], dict[int, list[dict]]]:
    """Return (issues, journals_by_issue)."""
    today = REPORT_DATE
    issues = [
        # Created by alice today -> 新增
        _issue(101, 1, 1, f"{today}T01:00:00Z", f"{today}T02:00:00Z", "BUG", 1),
        _issue(102, 2, 1, f"{today}T01:10:00Z", f"{today}T02:10:00Z", "功能", 2),
        _issue(
            103, 3, 1, f"{today}T01:20:00Z", f"{today}T02:20:00Z", "支持", 5
        ),
        # Alice changed the status today -> 复测
        _issue(
            107,
            1,
            1,
            "2026-09-01T03:00:00Z",
            f"{today}T02:30:00Z",
            "功能",
            5,
        ),
        # Alice only commented today, review_strict -> excluded
        _issue(
            104,
            1,
            2,
            "2026-09-02T03:00:00Z",
            f"{today}T02:40:00Z",
            "BUG",
            2,
        ),
        # Alice changed the status of a review issue -> 审核/复核
        _issue(
            105,
            2,
            2,
            "2026-09-03T03:00:00Z",
            f"{today}T02:50:00Z",
            "BUG",
            2,
        ),
        # Created by bob, assigned to alice, alice commented today -> 审核/复核
        _issue(
            106,
            3,
            2,
            "2026-09-04T03:00:00Z",
            f"{today}T03:00:00Z",
            "功能",
            5,
            assigned_to_id=1,
            subject="需要复核的功能",
        ),
        # Created by alice earlier, only bob updated today -> excluded
        _issue(
            108,
            1,
            1,
            "2026-09-05T03:00:00Z",
            f"{today}T03:10:00Z",
            "BUG",
            2,
        ),
        # Assigned to alice but only bob updated today -> excluded
        _issue(
            109,
            2,
            2,
            "2026-09-06T03:00:00Z",
            f"{today}T03:20:00Z",
            "功能",
            2,
            assigned_to_id=1,
        ),
        # Unrelated review candidate, no alice journal -> excluded
        _issue(
            112,
            2,
            3,
            "2026-09-07T03:00:00Z",
            f"{today}T03:30:00Z",
            "BUG",
            2,
        ),
        # Alice issue in a project that is NOT selected -> excluded
        _issue(
            113,
            4,
            1,
            f"{today}T03:40:00Z",
            f"{today}T03:40:00Z",
            "BUG",
            1,
        ),
        # Created today by alice, "支持" tracker, but later closed
        _issue(
            111,
            2,
            1,
            f"{today}T01:30:00Z",
            f"{today}T03:50:00Z",
            "支持",
            5,
        ),
    ]

    journals: dict[int, list[dict]] = {
        101: [],
        102: [],
        103: [],
        107: [
            _journal(
                1001,
                1,
                f"{today}T02:30:00Z",
                details=[_status_detail(1, 5)],
            )
        ],
        104: [
            _journal(1002, 1, f"{today}T02:40:00Z", notes="已看过，稍后处理")
        ],
        105: [
            _journal(
                1003,
                2,
                f"{today}T01:50:00Z",
                details=[_assignee_detail(0, 1)],
            ),
            _journal(
                1004,
                1,
                f"{today}T02:50:00Z",
                details=[_status_detail(2, 5)],
            ),
        ],
        106: [
            _journal(1005, 2, f"{today}T02:00:00Z", notes="请复核"),
            _journal(1006, 1, f"{today}T03:00:00Z", notes="复核通过"),
        ],
        108: [
            _journal(
                1007,
                2,
                f"{today}T03:10:00Z",
                details=[_status_detail(2, 5)],
            )
        ],
        109: [
            _journal(
                1008,
                2,
                f"{today}T03:20:00Z",
                details=[_status_detail(2, 5)],
            )
        ],
        112: [
            _journal(
                1009,
                3,
                f"{today}T03:30:00Z",
                details=[_status_detail(2, 5)],
            )
        ],
        113: [],
        111: [
            _journal(
                1010,
                2,
                f"{today}T03:00:00Z",
                details=[_status_detail(1, 2)],
            ),
            _journal(
                1011,
                1,
                f"{today}T03:50:00Z",
                details=[_status_detail(2, 5)],
            ),
        ],
    }
    return issues, journals


class MockRedmine:
    def __init__(self, delay: float = 0.04, search_delay: float = 0.0,
                 search_ids: list[int] | None = None, spam: int = 0,
                 own_spam: int = 0, legacy_updated_by: bool = False):
        self.delay = delay
        self.search_delay = search_delay
        self.search_ids = search_ids or []
        self.legacy_updated_by = legacy_updated_by
        self.issues, self.journals = build_fixture()
        for i in range(spam):
            iid = 2000 + i
            self.issues.append(_issue(
                iid, 2, 2, "2026-09-08T03:00:00Z",
                f"{REPORT_DATE}T04:{i // 60:02d}:{i % 60:02d}Z",
                "BUG", 2,
            ))
            self.journals[iid] = []
        for i in range(own_spam):
            iid = 3000 + i
            self.issues.append(_issue(
                iid, 3, 1, "2026-09-09T03:00:00Z",
                f"{REPORT_DATE}T05:{i // 60:02d}:{i % 60:02d}Z",
                "功能", 5,
            ))
            self.journals[iid] = [
                _journal(
                    5000 + i, 1,
                    f"{REPORT_DATE}T05:{i // 60:02d}:{i % 60:02d}Z",
                    details=[_status_detail(1, 5)],
                )
            ]
        self.by_id = {issue["id"]: issue for issue in self.issues}
        self.counts: Counter[str] = Counter()
        self.query_log: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        self.lock = threading.Lock()
        self.request_lock = threading.Lock()  # serialise like a 1-worker server
        self._start_server(self._handler())

    def _start_server(self, handler) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def start(self) -> "MockRedmine":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _parse(self, path: str):
        parsed = urlparse(path)
        return parsed.path, {k: v[-1] for k, v in parse_qs(parsed.query).items()}

    def _handler(self):
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _send(self, payload, status=200):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path, params = mock._parse(self.path)
                with mock.lock:
                    mock.counts[path] += 1
                    mock.query_log.append((path, tuple(sorted(params.items()))))
                if path == "/search.json":
                    # 搜索是慢查询，但不应该阻塞其它普通请求。
                    self._handle(path, params)
                    return
                # One request at a time, like a small Redmine behind a single
                # application worker.
                with mock.request_lock:
                    time.sleep(mock.delay)
                    self._handle(path, params)

            def _handle(self, path, params):
                if path == "/users/current.json":
                    self._send({
                        "user": {
                            "id": 1,
                            "login": "alice",
                            "firstname": "Alice",
                            "lastname": "",
                            "mail": "alice@example.com",
                        }
                    })
                    return

                if path == "/issue_statuses.json":
                    self._send({
                        "issue_statuses": [
                            {"id": sid, "name": name}
                            for sid, name in STATUSES.items()
                        ]
                    })
                    return

                if path == "/search.json":
                    time.sleep(mock.search_delay)
                    results = [
                        {
                            "id": iid,
                            "url": f"{mock.url}/issues/{iid}",
                            "type": "issue",
                            "title": mock.by_id[iid]["subject"],
                        }
                        for iid in mock.search_ids
                        if iid in mock.by_id
                    ]
                    self._send({"total_count": len(results), "results": results})
                    return

                if path == "/issues.json":
                    qs = parse_qs(urlparse(self.path).query)
                    ev_user = None
                    if (
                        "assigned_to_id" in qs.get("f[]", [])
                        and qs.get("op[assigned_to_id]", [""])[-1] == "ev"
                    ):
                        ev_values = qs.get("v[assigned_to_id][]", [])
                        if ev_values:
                            ev_user = int(ev_values[-1])
                    updated_by_user = None
                    if params.get("updated_by"):
                        try:
                            updated_by_user = int(params["updated_by"])
                        except (TypeError, ValueError):
                            pass
                    selected = []
                    for issue in mock.issues:
                        updated_filter = params.get("updated_on")
                        if updated_filter:
                            issue_date = issue["updated_on"][:10]
                            if updated_filter.startswith("><") and "|" in updated_filter:
                                start, end = updated_filter[2:].split("|", 1)
                                if not (start <= issue_date <= end):
                                    continue
                            elif not issue["updated_on"].startswith(updated_filter):
                                continue
                        if params.get("project_id") and (
                            issue["project"]["id"] != int(params["project_id"])
                        ):
                            continue
                        if params.get("author_id") and (
                            issue["author"]["id"] != int(params["author_id"])
                        ):
                            continue
                        assigned = (issue.get("assigned_to") or {}).get("id")
                        if params.get("assigned_to_id") and (
                            assigned != int(params["assigned_to_id"])
                        ):
                            continue
                        if ev_user is not None:
                            ever_assigned = assigned == ev_user
                            if not ever_assigned:
                                for journal in mock.journals.get(
                                    issue["id"], []
                                ):
                                    for detail in journal.get("details", []):
                                        if (
                                            detail.get("name")
                                            == "assigned_to_id"
                                            and str(detail.get("new_value"))
                                            == str(ev_user)
                                        ):
                                            ever_assigned = True
                                            break
                                    if ever_assigned:
                                        break
                            if not ever_assigned:
                                continue
                        if updated_by_user is not None:
                            if mock.legacy_updated_by:
                                continue
                            has_user_journal = any(
                                (journal.get("user") or {}).get("id")
                                == updated_by_user
                                for journal in mock.journals.get(
                                    issue["id"], []
                                )
                            )
                            if not has_user_journal:
                                continue
                        selected.append(issue)

                    limit = min(int(params.get("limit", 25)), 100)
                    offset = int(params.get("offset", 0))
                    page = selected[offset:offset + limit]
                    self._send({
                        "issues": page,
                        "total_count": len(selected),
                        "offset": offset,
                        "limit": limit,
                    })
                    return

                if path.startswith("/issues/") and path.endswith(".json"):
                    issue_id = int(path.split("/")[2].split(".")[0])
                    issue = mock.by_id.get(issue_id)
                    if issue is None:
                        self._send({"errors": ["not found"]}, 404)
                        return
                    payload = dict(issue)
                    payload["journals"] = mock.journals.get(issue_id, [])
                    self._send({"issue": payload})
                    return

                self._send({"errors": [f"not found: {path}"]}, 404)

        return Handler


class SlowMock(MockRedmine):
    """Mock used to verify that a stalled request is actually bounded."""

    def __init__(self):
        self.delay = 0.0
        self.search_delay = 0.0
        self.search_ids = []
        self.issues, self.journals = build_fixture()
        self.by_id = {issue["id"]: issue for issue in self.issues}
        self.counts: Counter[str] = Counter()
        self.query_log: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        self.lock = threading.Lock()
        self.request_lock = threading.Lock()
        self._start_server(self._slow_handler())

    def _slow_handler(self):
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                time.sleep(3)
                body = b'{"user":{"id":1,"login":"alice"}}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler


def _canonical(report):
    entries = []
    for e in report.entries:
        entries.append([
            e.issue_id,
            e.issue_subject,
            e.project_name,
            e.tracker_name,
            e.status_name,
            e.priority_name,
            e.updated_on,
            e.time_str,
            e.author_id,
            e.created_on,
        ])
    return {
        "user_name": report.user_name,
        "date": report.date,
        "weekday_cn": report.weekday_cn,
        "total_issues": report.total_issues,
        "project_count": report.project_count,
        "entries": entries,
    }


def run(package_root: str, runs: int = 2,
        project_ids: list[int] | None = (1, 2, 3),
        skip_review: bool = False,
        review_strict: bool = True,
        delay: float = 0.04,
        search_delay: float = 0.0,
        search_ids: list[int] | None = None,
        spam: int = 0,
        own_spam: int = 0,
        legacy_updated_by: bool = False) -> dict:
    sys.path.insert(0, str(Path(package_root).resolve()))
    from redmine_report.client import RedmineClient
    from redmine_report.generator import generate_report

    mock = MockRedmine(
        delay=delay, search_delay=search_delay, search_ids=search_ids,
        spam=spam, own_spam=own_spam,
        legacy_updated_by=legacy_updated_by,
    ).start()
    try:
        client = RedmineClient(url=mock.url, api_key="test-key")
        timings = []
        reports = []
        for _ in range(runs):
            before = dict(mock.counts)
            t0 = time.perf_counter()
            report = client.build_report_data(
                REPORT_DATE,
                project_ids=project_ids,
                skip_review=skip_review,
                review_strict=review_strict,
                support_always_new=False,
            )
            elapsed = time.perf_counter() - t0
            after = dict(mock.counts)
            requests = {
                key: after.get(key, 0) - before.get(key, 0)
                for key in set(after) | set(before)
            }
            timings.append(round(elapsed, 3))
            reports.append({
                "canonical": _canonical(report),
                "text": generate_report(
                    report,
                    custom_other="测试其他内容",
                    end_time="18:00",
                    report_with_numbers=True,
                ),
                "requests": requests,
            })
        return {
            "package_root": str(Path(package_root).resolve()),
            "scenario": {
                "project_ids": project_ids,
                "skip_review": skip_review,
                "review_strict": review_strict,
                "delay": delay,
                "search_delay": search_delay,
                "search_ids": search_ids,
                "spam": spam,
                "own_spam": own_spam,
                "legacy_updated_by": legacy_updated_by,
            },
            "timings": timings,
            "runs": reports,
            "total_requests": dict(mock.counts),
            "query_log": [
                {"path": path, "params": dict(params)}
                for path, params in mock.query_log
            ],
        }
    finally:
        mock.stop()


def timeout_check(package_root: str) -> dict:
    sys.path.insert(0, str(Path(package_root).resolve()))
    from redmine_report.client import RedmineClient

    mock = SlowMock().start()
    try:
        client = RedmineClient(url=mock.url, api_key="test-key", timeout=1)
        t0 = time.perf_counter()
        error = ""
        try:
            client.authenticate()
        except Exception as exc:  # noqa: BLE001 - benchmark records the error
            error = f"{type(exc).__name__}: {str(exc)[:120]}"
        return {
            "elapsed": round(time.perf_counter() - t0, 3),
            "error": error,
        }
    finally:
        mock.stop()


def historical_check(package_root: str) -> dict:
    """Regenerate a past report after its issues were updated again.

    Reproduces the reported bug: an issue that appeared in yesterday's
    report is updated today, so its ``updated_on`` moves past the report
    date.  The past report must still include it via the widened date range
    and journal check.
    """
    sys.path.insert(0, str(Path(package_root).resolve()))
    from redmine_report.client import RedmineClient

    next_day = (
        datetime.strptime(REPORT_DATE, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")
    mock = MockRedmine(delay=0.01).start()
    try:
        client = RedmineClient(url=mock.url, api_key="test-key")

        def entry_ids():
            report = client.build_report_data(
                REPORT_DATE,
                project_ids=[1, 2, 3],
                skip_review=False,
                review_strict=True,
            )
            return [e.issue_id for e in report.entries]

        before = entry_ids()
        # 这三条在报告日都被本人操作过；模拟它们当天之后又被更新。
        for iid in (105, 107, 111):
            mock.by_id[iid]["updated_on"] = f"{next_day}T02:00:00Z"
        # 让历史回归测试绕过“2分钟内复用整份日报”的缓存，
        # 确保它验证的是重新查询 + journal 校验。
        from redmine_report import client as client_module
        with client_module._REPORT_CACHE_LOCK:
            client_module._REPORT_CACHE.clear()
        after = entry_ids()
        return {
            "report_date": REPORT_DATE,
            "shifted_to": next_day,
            "before": before,
            "after": after,
            "missing": sorted(set(before) - set(after)),
        }
    finally:
        mock.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", default=".")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--timeout-check", action="store_true")
    parser.add_argument("--historical-check", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--log-queries", action="store_true")
    parser.add_argument("--projects", default="1,2,3",
                        help="comma separated project ids, or 'none'")
    parser.add_argument("--skip-review", action="store_true")
    parser.add_argument("--no-review-strict", action="store_true")
    parser.add_argument("--delay", type=float, default=0.04)
    parser.add_argument("--search-delay", type=float, default=0.0)
    parser.add_argument("--search-ids", default="",
                        help="comma separated ids returned by /search.json")
    parser.add_argument("--spam", type=int, default=0,
                        help="add N unrelated review candidates")
    parser.add_argument("--own-spam", type=int, default=0,
                        help="add N issues owned by alice with today's journal")
    parser.add_argument("--legacy-updated-by", action="store_true",
                        help="simulate a server without updated_by filter")
    args = parser.parse_args()

    project_ids = None if args.projects.lower() == "none" else [
        int(p) for p in args.projects.split(",") if p.strip()
    ]
    search_ids = [
        int(p) for p in args.search_ids.split(",") if p.strip()
    ]
    result = run(
        args.package_root,
        args.runs,
        project_ids=project_ids,
        skip_review=args.skip_review,
        review_strict=not args.no_review_strict,
        delay=args.delay,
        search_delay=args.search_delay,
        search_ids=search_ids,
        spam=args.spam,
        own_spam=args.own_spam,
        legacy_updated_by=args.legacy_updated_by,
    )
    if args.timeout_check:
        result["timeout"] = timeout_check(args.package_root)
    if args.historical_check:
        result["historical"] = historical_check(args.package_root)

    if args.json:
        if not args.log_queries:
            result.pop("query_log", None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"package: {result['package_root']}")
        print(f"timings: {result['timings']}")
        for i, run_result in enumerate(result["runs"], 1):
            print(f"run {i} requests: {run_result['requests']}")
            print(f"run {i} issues: {run_result['canonical']['total_issues']}")
        if "timeout" in result:
            print(f"timeout check: {result['timeout']}")
        if "historical" in result:
            print(f"historical check: {result['historical']}")
        print(f"all requests: {result['total_requests']}")
        if args.log_queries:
            for item in result["query_log"]:
                print(item)
        print(f"report text:\n{result['runs'][0]['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
