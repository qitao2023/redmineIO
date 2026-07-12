"""日报生成器 — 按新增/复测/审核·复核/其他分类生成纯文本日报。

分类规则：
- 新增：跟踪=支持 + 本人创建（不限状态）；或 状态=新建 + 本人创建
- 复测：状态≠新建 + 本人创建
- 审核/复核：创建者不是本人
- 其他：未归类的 Issue + 用户自定义内容
"""

import textwrap
from datetime import datetime

from .models import DailyReport, IssueEntryData

# 文本自动换行配置
_LINE_WIDTH = 80
_CONTINUATION_INDENT = "       "  # 续行缩进

SECTION_ORDER = ["新增", "复测", "审核/复核", "其他"]
SECTION_PREFIX = {
    "新增": "1）新增",
    "复测": "2）复测",
    "审核/复核": "3）审核/复核",
    "其他": "4）其他",
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
    return 99


def _format_entry(e: IssueEntryData, show_progress: bool = True) -> str:
    """格式化单条 Issue 为日报行，长行自动换行并缩进。"""
    tracker = e.tracker_name or ""
    status = f"({e.status_name})" if e.status_name else ""
    subject = e.issue_subject or ""
    line = f"   {tracker} #{e.issue_id} {status}: {subject}"
    if show_progress:
        line += " -100%；"

    if len(line) <= _LINE_WIDTH:
        return line

    wrapper = textwrap.TextWrapper(
        width=_LINE_WIDTH,
        subsequent_indent=_CONTINUATION_INDENT,
        break_long_words=False,
    )
    return wrapper.fill(line)


def _classify(entry: IssueEntryData, current_user_id: int, report_date: str = "") -> str:
    """根据状态和创建者分类 Issue。

    Returns:
        "新增" | "复测" | "审核/复核" | "其他" | "丢弃"
    """
    status = entry.status_name or ""
    tracker = entry.tracker_name or ""
    is_mine = entry.author_id and entry.author_id == current_user_id

    # 新增：跟踪=支持 + 本人 + 当日创建
    if tracker == "支持" and is_mine and entry.created_on == report_date:
        return "新增"

    # 新增：状态=新建 + 本人 + 当日创建
    if status == "新建" and is_mine and entry.created_on == report_date:
        return "新增"

    # 复测：状态≠新建 + 本人
    if status != "新建" and is_mine:
        return "复测"

    # 审核/复核：创建者不是本人
    if not is_mine and status:
        return "审核/复核"

    # 兜底：不做自动归类，丢弃
    return "丢弃"


def generate_report(
    report: DailyReport,
    custom_other: str = "",
    end_time: str | None = None,
) -> str:
    """生成纯文本日报。"""
    groups: dict[str, list[IssueEntryData]] = {
        "新增": [],
        "复测": [],
        "审核/复核": [],
    }

    for entry in report.entries:
        group = _classify(entry, report.current_user_id, report.report_date)
        groups.setdefault(group, []).append(entry)

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

        if group_key == "其他":
            # 其他只放用户自定义内容，不自动归类 Issue
            if not custom_other.strip():
                continue  # 没有自定义内容则不显示本节
            title = SECTION_PREFIX[group_key]
            lines.append(title)
            for other_line in custom_other.strip().split("\n"):
                lines.append(f"   {other_line}")
        else:
            title = f"{SECTION_PREFIX[group_key]}："
            lines.append(title)
            for e in entries:
                lines.append(_format_entry(e))

    # 底部
    lines.append("========")
    lines.append("考勤记录：")
    if end_time is None:
        end_time = datetime.now().strftime("%H:%M")
    lines.append(f"上班时间：08:30，下班时间：{end_time}")
    lines.append("中途外出记录")

    return "\n".join(lines) + "\n"
