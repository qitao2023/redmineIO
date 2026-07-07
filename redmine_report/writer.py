"""输出处理 — 将日报内容写入文件或打印到标准输出。"""

import sys
from pathlib import Path


def write_report(content: str, filepath: str | Path | None = None) -> Path:
    """将日报内容输出。

    Args:
        content: Markdown 格式的日报内容。
        filepath: 输出文件路径，None 则打印到 stdout。

    Returns:
        实际写入的文件路径（stdout 模式返回 Path("-")）。
    """
    if filepath is None or filepath == "-":
        sys.stdout.write(content)
        sys.stdout.write("\n")
        return Path("-")

    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
