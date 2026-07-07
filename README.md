# Redmine 日报生成工具

输入你的 Redmine API Token，自动拉取当日工时记录，生成格式化的 Markdown 工作日报。

**两种使用方式：**

| 方式 | 适用场景 | 需要 |
|------|----------|------|
| 🖥 **GUI 图形界面** | 日常手动操作 | Python 或 独立 exe |
| ⌨ **CLI 命令行** | 脚本自动化 | Python |

---

## 🖥 GUI 图形界面（推荐）

### 方式 A：直接运行 exe（无需安装 Python）

1. 下载或构建 `Redmine日报工具.exe`
2. 双击运行
3. 输入服务器地址和 API Key，选择日期，点击「生成日报」

### 方式 B：Python 源码运行

```bash
pip install -r requirements.txt
python run_gui.py
```

### GUI 界面说明

- **左侧面板**：填写连接信息
  - 服务器地址：Redmine 网址
  - API Key：个人访问密钥（带 👁 显示/隐藏）
  - 日期：默认今天，可修改
  - 📂 加载配置：从 config.yaml 自动填充
  - 🚀 生成日报：开始获取数据
  - 💾 保存日报：导出为 .md 文件
- **右侧面板**：日报内容实时预览
- **底部状态栏**：显示连接状态和统计摘要

---

## ⌨ CLI 命令行

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

复制配置文件模板并填入实际值：

```bash
cp config.yaml.example config.yaml
```

编辑 `config.yaml`：

```yaml
redmine_url: "https://your-redmine-server.com"
api_key: "your_api_key_here"        # Redmine → 我的账号 → API 访问键
timezone: "Asia/Shanghai"
```

也可以使用环境变量（优先于配置文件）：

```bash
export REDMINE_URL="https://your-redmine-server.com"
export REDMINE_API_KEY="your_api_key_here"
```

### 3. 使用

```bash
# 生成今天的日报（输出到 ./reports/日报-2026-07-07.md）
python -m redmine_report

# 指定日期
python -m redmine_report -d 2026-07-06

# 打印到终端
python -m redmine_report -s

# 预览数据摘要（不生成文件，用于调试）
python -m redmine_report --dry-run

# 自定义输出路径
python -m redmine_report -o ~/Desktop/日报.md

# 使用指定配置文件
python -m redmine_report -c /path/to/my-config.yaml

# 列出所有可访问项目
python -m redmine_report --list-projects
```

安装为全局命令（可选）：

```bash
pip install -e .
redmine-report -d 2026-07-06 -s
```

## 📦 构建独立 exe

将工具打包为单个 `.exe` 文件，可在没有 Python 的电脑上运行：

```bash
# 方式一：双击 build.bat（Windows）
build.bat

# 方式二：命令行
pip install pyinstaller
pyinstaller redmine_report.spec --clean --noconfirm
```

输出文件：`dist/Redmine日报工具.exe`（约 35MB）

## 日报格式

```markdown
# 张三 - 周一工作汇报（2026-07-06）

> 总计工时: 7.5h | 处理问题: 15 个 | 涉及项目: 2 个

## 1、技术支持问题
| 时间 | 项目 | 编号 | 状态 | 标题 | 工时 |
|------|------|------|------|------|------|
| ... | ... | ... | ... | ... | ... |

## 2、BUG库问题
...

## 3、功能开发
...

## 4、其他
...

---
**考勤记录：**
上班时间：--:--；下班时间：--:--
中途外出记录：无；
```

## 项目结构

```
redmineIO/
├── redmine_report/
│   ├── __init__.py          # 包声明
│   ├── __main__.py          # python -m 入口
│   ├── cli.py               # Click 命令行
│   ├── config.py            # 配置加载 (YAML + 环境变量)
│   ├── client.py            # Redmine API 封装
│   ├── models.py            # 数据模型
│   ├── generator.py         # Markdown 日报生成
│   └── writer.py            # 输出处理
├── config.yaml.example      # 配置文件模板
├── requirements.txt         # 依赖列表
├── setup.py                 # pip install -e .
└── README.md
```

## 配置说明

### 配置文件查找优先级

1. `-c` 参数指定的路径
2. 当前目录的 `config.yaml`
3. `~/.redmine_report/config.yaml`
4. `/etc/redmine_report/config.yaml`

### 环境变量覆盖

| 环境变量 | 对应配置 |
|----------|----------|
| `REDMINE_URL` | `redmine_url` |
| `REDMINE_API_KEY` | `api_key` |
| `REDMINE_TIMEZONE` | `timezone` |

## 获取 API Key

1. 登录 Redmine
2. 点击右上角 **我的账号**（My account）
3. 右侧边栏找到 **API 访问键**（API access key）
4. 点击 **显示**（Show），复制密钥

## 依赖

- Python >= 3.9
- [python-redmine](https://python-redmine.com/) — Redmine REST API 客户端
- [Click](https://click.palletsprojects.com/) — CLI 框架
- [PyYAML](https://pyyaml.org/) — YAML 配置解析
