#!/usr/bin/env python
"""Redmine 数据诊断工具 — 逐条打印某天所有的原始数据，帮助定位问题。

用法:
    python debug_data.py <Redmine地址> <API_Key> [日期]

示例:
    python debug_data.py http://192.168.1.100/redmine abcdef1234567890
    python debug_data.py http://192.168.1.100/redmine abcdef1234567890 2026-07-06
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from redminelib import Redmine
from redminelib.exceptions import AuthError, BaseRedmineError, ResourceNotFoundError


def debug(url: str, api_key: str, report_date: str):
    """逐条获取并打印所有数据，不做任何过滤。"""

    print("=" * 70)
    print(f"  Redmine 数据诊断")
    print(f"  服务器: {url}")
    print(f"  日期:   {report_date}")
    print("=" * 70)

    # 连接
    print("\n[1] 连接 Redmine...")
    try:
        rm = Redmine(url.rstrip("/"), key=api_key, requests={"timeout": 30})
        print("  ✓ 连接成功")
    except BaseRedmineError as e:
        print(f"  ✗ 连接失败: {e}")
        sys.exit(1)

    # 认证
    print("\n[2] 获取当前用户...")
    try:
        user = rm.user.get("current")
        user_id = user.id
        user_name = f"{getattr(user, 'lastname', '')}{getattr(user, 'firstname', '')}"
        print(f"  ✓ ID={user_id}, Name={user_name}, Login={getattr(user, 'login', '?')}")
    except AuthError:
        print("  ✗ API Key 无效")
        sys.exit(1)
    except BaseRedmineError as e:
        print(f"  ✗ 失败: {e}")
        sys.exit(1)

    # 获取时间条目
    print(f"\n[3] 获取 {report_date} 的工时记录...")
    try:
        entries = list(rm.time_entry.filter(
            user_id=user_id,
            spent_on=report_date,
            limit=200,
        ))
        print(f"  ✓ 共 {len(entries)} 条工时记录")
    except BaseRedmineError as e:
        print(f"  ✗ 获取失败: {e}")
        sys.exit(1)

    if not entries:
        print(f"\n  ⚠ {report_date} 没有任何工时记录！")
        print(f"  可能原因：")
        print(f"    1. 这天确实没有记录工时")
        print(f"    2. 日期格式不对（应为 YYYY-MM-DD）")
        print(f"    3. 该账号这天没有操作")
        print(f"\n  试试换一个确定有工时的日期，例如：")
        print(f"    python debug_data.py {url} {api_key} 2026-06-01")
        return

    # 逐条详情
    print(f"\n[4] 逐条工时记录详情:\n")

    for i, te in enumerate(entries, 1):
        print(f"  {'─' * 64}")
        print(f"  [{i}] TimeEntry #{te.id}")
        print(f"  {'─' * 64}")

        # 打印工时记录的所有原始字段
        print(f"  ⏱ 工时记录字段:")
        fields = {
            "id": te.id,
            "hours": float(te.hours),
            "comments": getattr(te, "comments", "") or "(空)",
            "spent_on": getattr(te, "spent_on", "?"),
            "activity": getattr(te, "activity", None),
            "activity.name": getattr(getattr(te, "activity", None), "name", "(无)"),
            "created_on": getattr(te, "created_on", "?"),
            "updated_on": getattr(te, "updated_on", "?"),
            "project": getattr(te, "project", None),
            "project.name": getattr(getattr(te, "project", None), "name", "(无)"),
            "issue": getattr(te, "issue", None),
        }
        for k, v in fields.items():
            print(f"      {k:20s} = {v}")

        # 检查 issue 属性
        te_issue = getattr(te, "issue", None)
        if te_issue is None:
            print(f"  ⚠ 没有关联的 Issue！（可能是纯工时记录，不关联任务）")
            print()
            continue

        # 尝试获取 issue_id
        try:
            issue_id = te_issue.id
            print(f"  📌 关联 Issue ID: {issue_id}")
        except Exception as e:
            print(f"  ✗ 获取 Issue ID 失败: {e}")
            print()

            # 尝试从 URL 提取
            try:
                raw = te._data
                if "issue" in raw:
                    print(f"  → 原始数据中的 issue: {json.dumps(raw['issue'], ensure_ascii=False, default=str)}")
            except Exception:
                pass
            continue

        # 获取 Issue 详情
        print(f"\n  📋 获取 Issue #{issue_id} 详情...")
        try:
            issue = rm.issue.get(issue_id)
            print(f"  ✓ Issue 详情:")
            issue_fields = {
                "id": issue.id,
                "subject": issue.subject,
                "status": getattr(issue.status, "name", "?"),
                "priority": getattr(issue.priority, "name", "?"),
                "tracker": getattr(issue.tracker, "name", "?"),
                "project": getattr(issue.project, "name", "?"),
                "author": getattr(issue.author, "name", "?"),
                "assigned_to": getattr(issue.assigned_to, "name", "?") if hasattr(issue, "assigned_to") else "?",
                "start_date": getattr(issue, "start_date", "?"),
                "due_date": getattr(issue, "due_date", "?"),
                "done_ratio": getattr(issue, "done_ratio", "?"),
                "created_on": getattr(issue, "created_on", "?"),
                "updated_on": getattr(issue, "updated_on", "?"),
            }
            for k, v in issue_fields.items():
                print(f"      {k:20s} = {v}")
        except ResourceNotFoundError:
            print(f"  ✗ Issue #{issue_id} 不存在（已删除）")
        except BaseRedmineError as e:
            print(f"  ✗ 获取失败: {e}")
        print()

    # 汇总
    print(f"  {'=' * 64}")
    print(f"  诊断完成。")
    print(f"  总计: {len(entries)} 条工时记录")
    print(f"  如果没有问题，数据应该如上所示。")
    print(f"  请对比上面输出和日报工具的输出，找出差异。")
    print(f"  {'=' * 64}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python debug_data.py <Redmine地址> <API_Key> [日期]")
        print()
        print("示例:")
        print("  python debug_data.py http://192.168.1.100/redmine abc123 2026-07-06")
        print()
        print("（日期可省略，默认今天）")
        sys.exit(1)

    url = sys.argv[1]
    api_key = sys.argv[2]
    report_date = sys.argv[3] if len(sys.argv) > 3 else date.today().isoformat()

    debug(url, api_key, report_date)
