"""日报生成器 — 严格按照 日报格式.txt 的格式生成纯文本日报。

问题按「跟踪」分三类：支持 → 功能 → BUG，对应 1/2/3 节。
未匹配的归入「其他」第 4 节。
"""

import textwrap
from datetime import datetime

from .models import DailyReport, IssueEntryData

# 文本自动换行配置
_LINE_WIDTH = 80
_CONTINUATION_INDENT = "       "  # 续行缩进，对齐"1、"后的第一个汉字

# Tracker 分组规则：按 Redmine「跟踪」字段匹配
TRACKER_GROUP_RULES: list[tuple[str, list[str]]] = [
    ("技术支持", ["支持", "Support", "helpdesk"]),
    ("功能", ["功能", "Feature", "feature", "开发", "dev"]),
    ("BUG", ["BUG", "缺陷", "bug"]),
    ("其他", []),
]

SECTION_ORDER = ["技术支持", "功能", "BUG", "其他"]
SECTION_PREFIX = {
    "技术支持": "1、技术支持",
    "功能": "2、功能",
    "BUG": "3、BUG",
    "其他": "4、其他",
}


# 优先级排序：数值越小越靠前
_PRIORITY_RANK: dict[str, int] = {
    "立刻": 0,
    "紧急": 1,
    "高": 2,
    "普通": 3,
    "低": 4,
}


def _priority_key(e: IssueEntryData) -> int:
    """获取 Issue 的优先级排序键。"""
    name = e.priority_name or ""
    for kw, rank in _PRIORITY_RANK.items():
        if kw in name:
            return rank
    return 99  # 未知优先级排最后


def _format_entry(idx: int, e: IssueEntryData) -> str:
    """格式化单条 Issue 为日报行，长行自动换行并缩进。"""
    time_str = e.time_str or "  :  "
    priority = f"[{e.priority_name}]" if e.priority_name else ""
    project = e.project_name or ""
    tracker = e.tracker_name or ""
    status = f"({e.status_name})" if e.status_name else ""
    subject = e.issue_subject or ""
    line = f"   {idx}) {priority} {time_str} {project} {tracker} #{e.issue_id} {status}: {subject}"

    if len(line) <= _LINE_WIDTH:
        return line

    wrapper = textwrap.TextWrapper(
        width=_LINE_WIDTH,
        subsequent_indent=_CONTINUATION_INDENT,
        break_long_words=False,
    )
    return wrapper.fill(line)


def _classify_tracker(tracker_name: str) -> str:
    """根据 Redmine「跟踪」字段匹配分组。"""
    if not tracker_name:
        return "其他"
    for group, keywords in TRACKER_GROUP_RULES:
        if not keywords:
            continue
        for kw in keywords:
            if kw.lower() in tracker_name.lower():
                return group
    return "其他"


def generate_report(report: DailyReport, custom_other: str = "", end_time: str | None = None) -> str:
    """生成纯文本日报。

    Args:
        report: 日报数据对象。
        custom_other: 用户自定义的「其他」内容。为空时第 4 节显示「无」。
    """
    groups: dict[str, list[IssueEntryData]] = {
        "技术支持": [],
        "功能": [],
        "BUG": [],
        "其他": [],
    }

    for entry in report.entries:
        group = _classify_tracker(entry.tracker_name)
        groups.setdefault(group, []).append(entry)

    # 每组内按优先级排序：立刻 > 紧急 > 高 > 普通 > 低
    for g in groups.values():
        g.sort(key=_priority_key)

    lines: list[str] = []

    # 头部
    lines.append(
        f"{report.user_name}-{report.weekday_cn}工作汇报（{report.date}）"
    )

    # 按顺序输出各分组
    for group_key in SECTION_ORDER:
        entries = groups.get(group_key, [])

        # 标题带总数
        count = len(entries)
        if group_key == "其他":
            title = SECTION_PREFIX[group_key]
        elif count > 0:
            title = f"{SECTION_PREFIX[group_key]}（{count}）"
        else:
            title = f"{SECTION_PREFIX[group_key]}（0）"
        lines.append(title)

        if group_key == "其他":
            # 第 4 节：归类 Issue + 用户自定义内容
            has_content = False
            for idx, e in enumerate(entries, 1):
                has_content = True
                line = _format_entry(idx, e)
                lines.append(line)

            if custom_other.strip():
                for other_line in custom_other.strip().split("\n"):
                    lines.append(f"   {other_line}")
                has_content = True

            if not has_content:
                lines.append("   无")
        else:
            for idx, e in enumerate(entries, 1):
                line = _format_entry(idx, e)
                lines.append(line)

    # 底部
    lines.append("=========")
    lines.append("考勤记录：")
    if end_time is None:
        end_time = datetime.now().strftime("%H:%M")
    lines.append(f"上班时间：08:30；下班时间：{end_time}")
    lines.append("中途外出记录：无；")

    return "\n".join(lines) + "\n"
