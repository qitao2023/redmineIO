#!/usr/bin/env python
"""Redmine 连接测试工具 — 独立脚本，用于诊断连接问题。

用法:
    python test_connection.py <Redmine地址> <API_Key>

示例:
    python test_connection.py http://192.168.1.100/redmine abcdef1234567890
"""

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from redmine_report.client import RedmineClient, RedmineClientError


def test(url: str, api_key: str):
    """逐步测试 Redmine 连接，打印详细信息。"""

    print("=" * 60)
    print("  Redmine 连接测试")
    print("=" * 60)
    print(f"  服务器: {url}")
    print(f"  API Key: {api_key[:8]}..." if len(api_key) > 8 else f"  API Key: {api_key}")
    print()

    # Step 1: 初始化连接
    print("[1/4] 正在连接服务器...")
    try:
        client = RedmineClient(url=url, api_key=api_key)
        print("  ✓ 连接对象创建成功")
    except RedmineClientError as e:
        print(f"  ✗ 连接失败: {e}")
        return False
    except Exception as e:
        print(f"  ✗ 未知错误: {e}")
        return False

    # Step 2: 验证 API Key
    print("[2/4] 正在验证 API Key...")
    try:
        user = client.authenticate()
        print(f"  ✓ 认证成功!")
        print(f"    用户ID: {user['id']}")
        print(f"    用户名: {user['login']}")
        print(f"    姓名:   {user['name']}")
        print(f"    邮箱:   {user['mail']}")
    except RedmineClientError as e:
        print(f"  ✗ 认证失败: {e}")
        return False
    except Exception as e:
        print(f"  ✗ 未知错误: {e}")
        return False

    # Step 3: 获取项目列表
    print("[3/4] 正在获取项目列表...")
    try:
        projects = client.list_projects()
        print(f"  ✓ 获取成功，共 {len(projects)} 个项目:")
        for p in projects[:10]:
            print(f"    [{p['id']}] {p['name']}")
        if len(projects) > 10:
            print(f"    ... 还有 {len(projects) - 10} 个项目")
    except RedmineClientError as e:
        print(f"  ✗ 获取失败: {e}")
        # 不 return False，这个步骤不影响后续
    except Exception as e:
        print(f"  ✗ 未知错误: {e}")

    # Step 4: 获取今日工时记录
    from datetime import date
    today = date.today().isoformat()
    print(f"[4/4] 正在获取今日工时记录 ({today})...")
    try:
        entries = client.get_time_entries(today, user["id"])
        print(f"  ✓ 获取成功，今日共 {len(entries)} 条工时记录")
        for te in entries[:5]:
            print(f"    #{te['issue_id']} | {te['hours']}h | {te['activity_name']}")
        if len(entries) > 5:
            print(f"    ... 还有 {len(entries) - 5} 条")
        if len(entries) == 0:
            print(f"    (今日无工时记录，这是正常的)")
    except RedmineClientError as e:
        print(f"  ✗ 获取失败: {e}")
        return False
    except Exception as e:
        print(f"  ✗ 未知错误: {e}")
        return False

    print()
    print("=" * 60)
    print("  ✓ 全部测试通过！连接正常工作。")
    print("=" * 60)
    return True


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python test_connection.py <Redmine地址> <API_Key>")
        print()
        print("示例:")
        print("  python test_connection.py http://192.168.1.100/redmine abcdef1234567890")
        print()
        print("获取 API Key: Redmine → 我的账号 → API 访问键 → 显示")
        sys.exit(1)

    url = sys.argv[1]
    api_key = sys.argv[2]
    success = test(url, api_key)
    sys.exit(0 if success else 1)
