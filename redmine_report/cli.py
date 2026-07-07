"""Click 命令行接口 — 将所有模块串联为可用命令。"""

import sys
from datetime import date

import click

from .config import ConfigError, load_config
from .client import RedmineClient, RedmineClientError
from .generator import generate_report
from .writer import write_report


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

    # 3. --test-connection
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

    # 4. 获取数据
    click.echo(f"正在获取 {date} 的工作记录...", err=True)
    try:
        report = client.build_report_data(date)
    except RedmineClientError as e:
        click.secho(str(e), fg="red", err=True)
        sys.exit(1)

    # 5. --dry-run
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

    # 6. 生成报告
    content = generate_report(report)

    # 7. 输出
    if to_stdout:
        write_report(content)
    else:
        if output is None:
            output = f"{cfg.output_dir}/日报-{date}.md"
        path = write_report(content, output)
        click.echo(f"日报已生成: {path}", err=True)


if __name__ == "__main__":
    main()
