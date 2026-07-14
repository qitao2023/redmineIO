"""诊断脚本：查看指定 Issue 的 journal details 原始结构。
用法: python check_journal.py <Issue_ID> [日期 YYYY-MM-DD]
"""
import sys
from pathlib import Path

# 加项目路径
sys.path.insert(0, str(Path(__file__).resolve().parent))

from redminelib import Redmine
from config import load_config

cfg = load_config()
rm = Redmine(cfg.redmine_url, key=cfg.api_key, requests={"timeout": 30})

issue_id = int(sys.argv[1]) if len(sys.argv) > 1 else 100298
report_date = sys.argv[2] if len(sys.argv) > 2 else "2026-07-11"

user = rm.user.get("current")
user_id = user.id
print(f"用户: {user.lastname}{user.firstname} (ID={user_id})")
print(f"目标日期: {report_date}")
print()

issue = rm.issue.get(issue_id, include="journals")
print(f"Issue #{issue.id}: {issue.subject}")
print(f"  当前状态: {issue.status.name}")
print(f"  作者ID: {issue.author.id if hasattr(issue, 'author') else '?'}")
print(f"  journals 数量: {len(list(issue.journals))}")
print()

journals = list(issue.journals)
journals.reverse()  # 最新在前

for j in journals:
    try:
        ju = j.user
        jid = ju.id if ju else None
    except:
        jid = None
    jd = str(j.created_on)[:19] if hasattr(j, 'created_on') else "?"
    print(f"--- journal (user_id={jid}, time={jd}) ---")

    details = list(getattr(j, "details", []))
    print(f"  details 数量: {len(details)}")
    for d in details:
        print(f"  detail: property={getattr(d, 'property', '?')!r}, "
              f"name={getattr(d, 'name', '?')!r}, "
              f"old_value={getattr(d, 'old_value', '?')!r}, "
              f"new_value={getattr(d, 'new_value', '?')!r}")
    print()
