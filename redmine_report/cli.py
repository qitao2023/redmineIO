"""Click 命令行接口 — 将所有模块串联为可用命令。"""

import sys
from datetime import date
from pathlib import Path

import click
import yaml

from .config import ConfigError, load_config
from .client import RedmineClient, RedmineClientError
from .generator import generate_report
from .writer import write_report


def _get_config_write_path() -> Path:
    """获取 config.yaml 写入路径。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config.yaml"
    return Path("config.yaml")


def _save_project_ids(project_ids: list[int], config_path: Path) -> None:
    """将 project_ids 写入配置文件（保留已有字段）。"""
    existing: dict = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
        except Exception:
            pass

    existing["project_ids"] = project_ids

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(existing, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


@click.command()
@click.option(
    "-d", "--date",
    default=date.today().isoformat(),
    help="报告日期 YYYY-MM-DD（默认：今天）",
)
@click.option(
    "-o", "--output",
    type=click.Path(),
    help="输出文件路径（默认：./reports/日报-{date}.md）",
)
@click.option(
    "-c", "--config",
    "config_path",
    type=click.Path(exists=True),
    help="配置文件路径",
)
@click.option(
    "-s", "--stdout", "to_stdout",
    is_flag=True,
    help="打印到标准输出而非写入文件",
)
@click.option(
    "--list-projects", "list_projects_flag",
    is_flag=True,
    help="列出所有可访问项目并退出",
)
@click.option(
    "--setup",
    is_flag=True,
    help="交互式选择要跟踪的项目，写入 config.yaml 后退出",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="仅获取数据并打印摘要，不生成完整报告",
)
@click.option(
    "--test-connection", "test_connection_flag",
    is_flag=True,
    help="测试 Redmine 连接并显示诊断信息",
)
def main(
    date: str,
    output: str | None,
    config_path: str | None,
    to_stdout: bool,
    list_projects_flag: bool,
    setup: bool,
    dry_run: bool,
    test_connection_flag: bool,
):
    """从 Redmine Issues 生成 Markdown 日报。

    \b
    示例：
      redmine-report                          今天的日报
      redmine-report -d 2026-07-06 -s         指定日期，打印到终端
      redmine-report -o ~/Desktop/日报.md      自定义输出路径
      redmine-report --dry-run                预览数据摘要
      redmine-report --list-projects          列出可访问项目
      redmine-report --setup                  重新选择要跟踪的项目
      redmine-report --setup                  选择要跟踪的项目
    """
    # 1. 加载配置
    try:
        cfg = load_config(config_path=config_path)
    except ConfigError as e:
        click.secho(str(e), fg="red", err=True)
        sys.exit(1)

    # 2. 连接 Redmine
    click.echo(f"正在连接 Redmine: {cfg.redmine_url} ...", err=True)
    try:
        client = RedmineClient(
            url=cfg.redmine_url,
            api_key=cfg.api_key,
            timeout=cfg.requests_timeout,
        )
    except RedmineClientError as e:
        click.secho(str(e), fg="red", err=True)
        sys.exit(1)

    # 3. --setup: 交互式选择项目
    if setup:
        _do_setup(client, config_path)
        return

    # 3. --test-connection / --setup
    if test_connection_flag:
        click.echo("=== Redmine 连接测试 ===\n")
        click.echo(f"服务器: {cfg.redmine_url}")

        # 测试认证
        try:
            user = client.authenticate()
            click.echo(f"认证:   ✓ 成功")
            click.echo(f"用户:   {user['name']} ({user['login']})")
            click.echo(f"ID:     {user['id']}")
        except RedmineClientError as e:
            click.secho(f"认证:   ✗ 失败 — {e}", fg="red")
            sys.exit(1)

        # 测试项目访问
        try:
            projects = client.list_projects()
            click.echo(f"项目:   ✓ 可访问 {len(projects)} 个项目")
        except RedmineClientError as e:
            click.secho(f"项目:   ✗ 获取失败 — {e}", fg="yellow")

        # 测试 Issue 获取
        from datetime import date as dt_date
        click.echo(f"Issue:  正在查询 {dt_date.today().isoformat()} 的 Issue...")
        try:
            issues = client.get_issues_by_date(dt_date.today().isoformat(), user["id"])
            click.echo(f"Issue:  ✓ {len(issues)} 个问题")
        except RedmineClientError as e:
            click.secho(f"Issue:  ✗ 获取失败 — {e}", fg="yellow")

        click.echo("\n✓ 连接测试完成")
        return

    # 4. --list-projects
    if list_projects_flag:
        click.echo("可访问项目列表：", err=True)
        try:
            projects = client.list_projects()
            for p in projects:
                click.echo(f"  [{p['id']}] {p['name']} ({p['identifier']})")
        except RedmineClientError as e:
            click.secho(str(e), fg="red", err=True)
            sys.exit(1)
        return

    # 5. 未配置 project_ids 时 → 自动弹出项目选择
    if not cfg.project_ids:
        if sys.stdin.isatty():
            click.echo()
            click.secho("⚡ 首次使用：请选择要跟踪的项目", fg="cyan", err=True)
            _do_setup(client, config_path)
            # 重新加载配置以获取刚写入的 project_ids
            try:
                cfg = load_config(config_path=config_path)
            except ConfigError:
                pass
        else:
            click.secho(
                "⚠ 未配置 project_ids，将自动获取所有项目（建议先运行 --setup）",
                fg="yellow", err=True,
            )

    # 6. 显示当前跟踪的项目
    if cfg.project_ids:
        proj_ids = cfg.project_ids
        try:
            all_projects = {p["id"]: p["name"] for p in client.list_projects()}
            names = [all_projects.get(pid, f"#{pid}") for pid in proj_ids]
            click.echo(f"跟踪项目 ({len(proj_ids)}个): {', '.join(names)}", err=True)
            click.echo(f"  （修改选项目: redmine-report --setup）", err=True)
        except Exception:
            pass
    else:
        proj_ids = None

    # 7. 获取数据
    click.echo(f"正在获取 {date} 的工作记录...", err=True)
    try:
        report = client.build_report_data(date, project_ids=proj_ids,
                                            skip_review=cfg.skip_review,
                                            review_strict=cfg.review_strict)
    except RedmineClientError as e:
        click.secho(str(e), fg="red", err=True)
        sys.exit(1)

    # 8. --dry-run
    if dry_run:
        click.echo(f"\n=== 数据摘要 ({date}) ===")
        click.echo(f"  用户:     {report.user_name}")
        click.echo(f"  周几:     {report.weekday_cn}")
        click.echo(f"  Issue 数: {len(report.entries)} 个")
        click.echo(f"  问题数:   {report.total_issues} 个")
        click.echo(f"  项目数:   {report.project_count} 个")
        if report.entries:
            click.echo(f"\n  条目预览（前 5 条）：")
            for e in report.entries[:5]:
                click.echo(
                    f"    {e.time_str} #{e.issue_id} [{e.tracker_name}] "
                    f"{e.issue_subject[:50]} ({e.status_name})"
                )
        return

    # 9. 生成报告
    content = generate_report(report)

    # 10. 输出
    if to_stdout:
        write_report(content)
    else:
        if output is None:
            output = f"{cfg.output_dir}/日报-{date}.md"
        path = write_report(content, output)
        click.echo(f"日报已生成: {path}", err=True)


def _do_setup(client: RedmineClient, cli_config_path: str | None) -> None:
    """交互式项目选择：拉取用户项目 → 展示列表 → 用户勾选 → 写入 yaml。"""
    click.echo()
    click.echo("=== 项目跟踪设置 ===")
    click.echo()

    # 认证
    try:
        user = client.authenticate()
        click.echo(f"用户: {user['name']} (ID={user['id']})")
    except RedmineClientError as e:
        click.secho(str(e), fg="red", err=True)
        sys.exit(1)

    # 拉取项目
    click.echo("正在获取您的项目列表...")
    project_ids = client._get_user_project_ids(user["id"])
    if not project_ids:
        click.secho("未找到任何项目，请确认 Redmine 账号权限。", fg="yellow", err=True)
        sys.exit(1)

    # 获取项目名称
    all_projects = {p["id"]: p for p in client.list_projects()}

    # 展示列表
    click.echo(f"\n找到 {len(project_ids)} 个项目（默认全部勾选）：\n")
    for i, pid in enumerate(project_ids, 1):
        name = all_projects.get(pid, {}).get("name", f"项目#{pid}")
        click.echo(f"  [{i}] {name}")

    click.echo()
    click.echo("输入要去掉的项目编号（逗号分隔），或直接回车确认全部：")
    choice = click.prompt("", default="").strip()

    # 解析选择
    if choice:
        try:
            exclude_indices = {
                int(x.strip()) - 1 for x in choice.split(",") if x.strip()
            }
        except ValueError:
            click.secho("输入格式有误，已取消。", fg="red", err=True)
            return
        selected = [
            pid for i, pid in enumerate(project_ids) if i not in exclude_indices
        ]
    else:
        selected = project_ids

    if not selected:
        click.secho("没有选中任何项目，已取消。", fg="yellow", err=True)
        return

    # 写入配置
    write_path = _get_config_write_path()
    _save_project_ids(selected, write_path)

    click.echo()
    click.echo(f"✓ 已保存 {len(selected)} 个项目到 {write_path}")
    for pid in selected:
        name = all_projects.get(pid, {}).get("name", f"项目#{pid}")
        click.echo(f"  - {name}")


if __name__ == "__main__":
    main()
