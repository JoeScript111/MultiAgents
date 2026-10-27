# 多智能体数据分析与 HTML 报告

命令行持续对话系统：Main Agent 通过 DeepAgents `task` 工具依次调用 DS Analyst 与 Report Designer。前者在 Docker 中执行 Python 分析，后者通过确定性渲染器生成可离线打开的 HTML 报告。

## 安装与运行

在项目根目录的 PowerShell 中执行：

```powershell
conda activate prompt2
python -m pip install -r requirements-analysis.txt
# 先启动 Docker Desktop，使用 Linux containers。
docker build -f analysis_report/Dockerfile -t analysis-report:0.1 .
python -m analysis_report --data examples/sales.csv --question "分析销售概况、地区对比和月度趋势，生成中文报告"
```

也可不激活环境，直接用指定解释器：

```powershell
& 'C:\ProgramData\anaconda3\envs\prompt2\python.exe' -m analysis_report --data examples/sales.csv
```

沿用项目 `.env` 的 `MODEL`、`MODEL_PROVIDER`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`，不会改写原文件。默认 OpenAI 兼容 Chat Completions；模型必须支持工具调用。当前项目已配置的模型无需另外指定。

多个输入可重复使用 `--data`，路径包含空格时加引号：

```powershell
python -m analysis_report --data "data/销售.xlsx" --data "data/客户.csv" --question "先检查文件结构，关联关系不清楚时向我提问"
```

Excel 先检查工作表；CSV 支持 UTF-8（含 BOM）及 GB18030。不会默认合并多表。需要澄清时直接在下一条消息回答；退出后没有会话恢复。

首次任务完成后可输入：

- `只把报告改为深色主题，标题改为销售概况`：复用计算结果，新增报告版本。
- `补充各产品的销售额占比`：重新委派分析，再生成新报告。
- `/files`：列出输入副本、最新分析、所有报告。
- `/help`：显示帮助。
- `/exit` 或 Ctrl+C：退出并清理容器；已有文件保留。

管道输入也受支持，适合可重复验收；输入结束后自动退出。

## 架构与证据

```text
CLI + InMemorySaver
       |
   Main Agent (create_deep_agent)
       | task(ds-analyst)
       v
   DS Analyst -- inspect_data / run_analysis --> Docker Python
       |                                      /inputs 只读
       | validated AnalysisResult             /shared 本次执行工作区
       v
   Main Agent
       | task(report-designer)
       v
   Report Designer -- read_analysis / render_report --> 离线 HTML
       |
   Main Agent -- inspect_artifacts --> 用户
```

两个子 Agent 用独立工具集合构建为 `CompiledSubAgent` 形式。主 Agent 的内置文件工具仅操作内存状态，无法访问宿主项目；默认通用子 Agent 被禁用。子 Agent 没有任意宿主 shell 或文件写入工具。

分析脚本通过 `run_analysis(code)` 写入专用目录，在容器执行后产生 `result.json`。`AnalysisResult` 校验数据来源、指标、表格形状、图表引用、发现证据及有限数值；原始尺寸必须与预先检查的数据概况一致。一次用户消息最多三次执行，包括结果校验失败。失败时禁止生成新报告。

报告设计参数只有标题、摘要、主题和图表顺序。数值与图表来自已验证分析；摘要中的数值用 `{{metric:ID}}` 引用。渲染器转义文本并嵌入 Plotly，报告不依赖 CDN、网络或本地服务器。每个报告版本保存对应的设计参数和分析路径。

“已验证”表示脚本成功执行且满足数据契约，并不构成统计方法和业务解释完全正确的证明。脚本和指标口径会保留供复核；相关性不会被当作因果关系。需要新计算或变更筛选条件时必须重新分析。

## 文件与配置

默认产物目录：`output/analysis_report/<时间戳_运行ID>/`。

| 路径 | 内容 |
| --- | --- |
| `manifest.json` | 输入清单、原始文件名、大小与 SHA-256 |
| `inputs/` | 输入副本，容器只读挂载 |
| `sandbox/` | 容器工作区；保留脚本执行现场 |
| `analyses/analysis_<轮次>_<尝试>/` | 原始脚本、原始 JSON、校验后结果及来源信息 |
| `reports/report_vNNN.html` | 可单独复制分享的报告 |
| `reports/report_vNNN.json` | 报告设计与分析版本关联 |
| `logs/` | 执行输出、事件与错误记录 |

可在 `.env` 中追加以下可选配置。容器设置使用 `ANALYSIS_` 前缀，不受现有示例的 `DOCKER_IMAGE` 或 `WORKSPACE_DIR` 影响。

```dotenv
ANALYSIS_DOCKER_IMAGE=analysis-report:0.1
ANALYSIS_TIMEOUT=120
ANALYSIS_MEMORY=2g
ANALYSIS_CPUS=2
# 相对路径以启动目录为基准；默认是项目 output/analysis_report。
# ANALYSIS_OUTPUT_DIR=D:/reports
```

构建镜像需要网络；运行分析时容器禁用外网、根文件系统只读、移除 Linux capabilities，只挂载本次运行的沙盒目录和只读输入目录。模型 API 在宿主 `prompt2` 环境调用，模型凭据不会注入容器。数据概况、最多五行样本、必要的聚合结果和用户对话会发送给配置的模型服务。

数据集在容器内整体读取，默认内存 2 GB，面向可放入内存的 CSV/Excel；大数据集需先聚合或提高限制。聚合结果 JSON 限制 2 MB，单表最多 1000 行，报告表格最多显示 200 行。分析产物与日志会一直保留，按需自行清理。

## 测试与排错

```powershell
python -m pytest tests/test_analysis_report.py -q
# Docker 构建完成后运行实际容器测试；不调用真实模型。
python -m pytest tests/test_analysis_report_docker.py -q --run-docker
```

- Docker 引擎不可用：启动 Docker Desktop 并等待 Linux 引擎就绪；程序不会回退到本机执行代码。
- 镜像缺失：执行上面的 `docker build`。基础镜像默认 `python:3.12-bookworm`；可用 `--build-arg PYTHON_BASE=<兼容的 Python 3.12 Debian 镜像>` 更换来源。
- 模型认证、限流或超时：查看本次运行 `logs/turn_*_error.txt`，修复配置后重试。不要公开日志或数据文件。
- 多工作表或指标口径不明确：直接在命令行补充说明。
- 代码或数据契约失败：Agent 最多修正两次；仍失败会保留脚本与错误日志，不生成当前轮次的成功报告。

现有示例脚本、HTML skills 和 `.env` 不做修改。当前报告使用结构化参数和固定渲染器，不执行模型生成的任意 HTML/JavaScript。

### 本次实现验收（2026-10-26）

- `prompt2` 环境下 21 项测试通过，包含实际 Docker 读取 CSV/GB18030/多工作表 Excel、真实统计、输入只读、超时、模型异常和退出清理。
- 使用项目配置的真实模型完成销售示例分析和深色主题修改：总销售额 1100，缺失金额 1 条；华东 900、华南 200；月度金额 300、300、500。
- 两版 HTML 保留在 `output/analysis_report/20261026_173547_7e6dcc29/reports/`；第二轮没有重新执行分析。
- 浏览器通过本地 HTTP 预览确认柱状图与折线图正常，503 像素窄屏没有页面横向溢出，HTML 无外部资源引用。自动化浏览器不允许 `file://` 导航，因此直接双击文件的方式未在该浏览器中验证。
- 测试容器及临时预览服务器均已清理；构建好的 `analysis-report:0.1` 镜像保留供使用。
