"""数据模型 — 解耦 API 层与报表层。"""

from dataclasses import dataclass, field


@dataclass
class IssueEntryData:
    """一条 Issue 记录的结构化数据。"""

    issue_id: int
    issue_subject: str
    project_name: str
    tracker_name: str  # e.g. "BUG", "功能", "支持"
    status_name: str  # e.g. "新建", "已关闭", "已解决"
    priority_name: str  # e.g. "高", "普通", "低"
    updated_on: str  # 最后更新时间, "YYYY-MM-DDTHH:MM:SSZ"
    time_str: str  # 从 updated_on 提取的 HH:MM
    author_id: int = 0  # Issue 创建者 ID


@dataclass
class DailyReport:
    """一份完整的日报。"""

    user_name: str
    date: str  # YYYY-MM-DD
    weekday_cn: str  # 周一 ~ 周日
    entries: list[IssueEntryData] = field(default_factory=list)
    total_issues: int = 0
    project_count: int = 0
    current_user_id: int = 0  # 当前用户 ID，用于判断复测/复核
