"""安装脚本 — 使 redmine-report 成为全局可执行命令。"""

from setuptools import find_packages, setup

setup(
    name="redmine-report",
    version="1.0.0",
    description="Redmine 日报生成工具 — 输入 API Token，自动生成 Markdown 工作日报",
    author="",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "click>=8.0.0",
        "python-redmine>=2.5.0",
        "PyYAML>=6.0",
        "pywebview>=5.0",
        "flask>=3.0",
        "pywin32>=305",
    ],
    entry_points={
        "console_scripts": [
            "redmine-report=redmine_report.cli:main",
        ],
    },
)
