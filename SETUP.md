# 知源内容工作台 v0.13.0 — 安装与运行

本指南对应 `lin-yi-yi/zhiyuan-content-agent` 的最新 `main` 分支。当前范围是本机运行与单机小团队试点，能力及验证边界见 [README](README.md)。

## 1. 准备环境

| 项目 | 要求 |
| --- | --- |
| 系统 | macOS 或 Linux；Windows 请在 WSL2 的 Linux 环境执行以下 Bash 命令，未验证 Windows 原生运行 |
| Python | 3.12 或更新版本，能够创建 `venv` |
| Node.js | 22，包含 npm |
| 数据库 | 默认 SQLite，无需先安装 MySQL 或 Docker |

首次安装依赖需要网络。默认语义检索还会下载中文向量模型；无需安装本地大语言模型。

## 2. 获取项目并安装依赖

新用户执行：

```bash
git clone --branch main https://github.com/lin-yi-yi/zhiyuan-content-agent.git
cd zhiyuan-content-agent

# 只在文件不存在时创建，保留已有配置与凭证。
if [ ! -f .env ]; then cp .env.example .env; fi
./scripts/setup.sh
```

已有本地 `ai-content-agent` 目录可以继续使用，不必重命名或重新克隆。更新前先用 `git status` 核对本地改动；保留现有 `.env`、`.env.saas` 和 `.data/`。

安装脚本在项目根目录使用 `.venv`，按 `backend/requirements.lock.txt` 安装后端依赖，并执行前端 `npm ci`。有 `uv` 时使用它，否则使用虚拟环境内的 pip。需要指定解释器时运行：

```bash
PYTHON_BIN=/实际路径/python3.12 ./scripts/setup.sh
```

已有 `.venv` 会被复用；如果它由旧版 Python 创建，应先停止服务，保留备份后重新创建虚拟环境。仅更改 `PYTHON_BIN` 不会替换已有环境。

## 3. 启动本地工作台

在项目根目录执行：

```bash
./scripts/start.sh
```

打开 [本地工作台](http://127.0.0.1:8765)。脚本先构建前端，再用一个 Uvicorn worker 同时提供页面与接口，只监听 `127.0.0.1`；无需另开 Vite。按 Ctrl+C 停止，数据保留在 `.data/`：

- `.data/demo.db`：默认业务数据库。
- `.data/qdrant/`：默认向量索引。
- `.data/models/`：默认向量模型缓存。

更新或迁移前，用 [本地备份恢复工具](docs/local-backup.md) 显式选择当前数据库和索引，停机后备份，再恢复到新目录验证。默认本地归档与团队模式归档不互换。

`local` 与检索模式是两个独立选择：

| 配置 | 含义 |
| --- | --- |
| 生成供应商 `local`（默认） | 规则整理及原文摘录，不调用生成模型 API，也不是本地大语言模型 |
| `semantic`（启动脚本默认） | 使用 FastEmbed 中文向量模型与 Qdrant Local；首次使用需下载模型 |
| `lexical` | 使用词项检索，不需下载向量模型，适合先跑轻量演示 |

暂时不能下载向量模型时，显式启动词项模式：

```bash
RAG_RETRIEVAL_MODE=lexical ./scripts/start.sh
```

语义检索失败不会自动降级。词项索引切回语义检索需要重建索引；同一 Qdrant Local 数据目录只允许一个进程使用。`local` 不表示整个应用离线，信源读取仍可能联网。

最小使用顺序：导入有权使用的资料并核验 → 品牌绑定知识库 → 创建产品答疑任务 → 查看引用、编辑草稿 → 人工批准 → 下载正式交付清单。批准后修改正文或卡片会使旧批准失效，需重新审核。

## 4. 可选：在线模型与团队模式

在线生成：参考 [.env.example](.env.example) 在 `.env` 填写所需供应商的凭证、接口地址和模型名，重启后在「设置与连接」测试，再为任务选择供应商。模型名以账户实际可用项为准；选用在线模型会将任务及相关资料发送给该服务。调用估算与供应商账单的区别见 [调用追踪说明](docs/model-call-tracing.md)。

团队试点使用独立配置和数据：

```bash
if [ ! -f .env.saas ]; then cp .env.saas.example .env.saas; fi
(cd frontend && npm run build)
./scripts/start_saas.sh
```

打开 [团队入口](http://127.0.0.1:8766)。示例允许注册账号和组织；团队数据默认在 `.data/saas/`，不会自动导入或覆盖 `.data/demo.db`。团队脚本读取 `.env.saas`，不读取本地 `.env`，同样只监听本机并使用单 worker。需要词项模式时也可在命令前设置 `RAG_RETRIEVAL_MODE=lexical`。

备份恢复、权限与公开访问准备见 [团队运维指南](docs/saas-operations.md)；本机启动不等于已完成公网生产验收。

## 5. 检查与练习

```bash
./scripts/check.sh
```

此脚本隔离数据库与环境配置，执行后端测试、Python 编译检查、前端行为测试及构建，不以真实模型调用验证内容质量。需要亲自操作导入、失败重试、修改重审与导出时，按 [工业 FAQ 练习](docs/industrial-faq-practice.md) 启动独立临时合成环境；不要用客户资料做失败注入实验。

`check.sh` 会在子进程中禁用 dotenv，清空继承的模型密钥、固定本地任务模型和空价格表，并关闭 LangSmith/LangChain 外部追踪；不会修改你的配置文件。前端依赖审计可单独运行 `cd frontend && npm audit --registry=https://registry.npmjs.org`，GitHub CI 同样执行并阻止已知 moderate 及以上告警。

页面业务回归另用 `(cd frontend && npm run test:e2e)`；首次需安装 Chromium，详情见 [浏览器业务回归](docs/browser-regression.md)。按 [学习实验](docs/learning-labs.md) 记录自己运行、排错和修改的证据；后续开发顺序见 [路线图](ROADMAP.md)。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 提示 Python 版本不符或依赖缺失 | 确认 `.venv/bin/python --version` 为 3.12+；按上面的解释器说明处理，再运行 `setup.sh` |
| 前端安装或构建失败 | 确认 `node --version` 为 22，查看首次报错；安装依赖后重试，不必重装数据库 |
| 8765 被占用 | 若是已有本项目服务，先停止旧实例；若是其他应用占用，可用 `PORT=8770 ./scripts/start.sh` 换端口 |
| 提示同一 SQLite 数据库已有服务运行 | 先停止原服务，再启动新实例；换端口不能让两个实例同时操作同一业务库。服务退出会释放锁，不要删除锁文件强行启动。该保护只针对本地文件 SQLite，不是跨机器部署方案 |
| 团队端口需修改 | 同时设置 `SAAS_PORT` 和对应的 `SAAS_PUBLIC_ORIGIN`，例如 `SAAS_PORT=8771 SAAS_PUBLIC_ORIGIN=http://127.0.0.1:8771 ./scripts/start_saas.sh` |
| 向量模型下载失败 | 查看后端日志，使用上面的 `lexical` 命令继续本地流程；下载恢复后重建语义索引 |
| 页面仍是旧版本 | `start.sh` 会重新构建；团队模式修改前端后先在 `frontend` 目录执行 `npm run build`，再刷新页面；后端代码修改后重启服务 |
| 意外连接旧 MySQL | 检查终端是否已导出 `DATABASE_URL`；启动脚本会保留该环境变量。默认 SQLite 不要求 MySQL |
| 页面打不开或任务失败 | 核对终端日志、当前端口及浏览器请求；本地模式可检查 `/api/health` 和 `/api/ready`，接口正常仍需实际跑完审核交付流程 |

`/api/health` 返回当前运行版本；`/api/ready` 检查数据库可用性，连接失败返回 503。团队模式下 readiness 需要已登录的组织上下文。项目交接与定向测试入口见 [开发指南](CONTRIBUTING.md)，完整文档分组见 [文档导航](docs/README.md)。

更完整的能力、限制及验证记录以 [README](README.md) 和 [v0.13.0 工程验收记录](docs/validation/v013-reliability-2026-10-04.md) 为准。容器升级与快照回退按 [专用说明](docs/container-acceptance.md)在隔离环境进行；排错时用 [请求关联诊断](docs/diagnostics.md)核对执行尝试。问答范围确认见 [参数目录说明](docs/question-clarification.md)，未知用量处置见 [结算故障说明](docs/usage-finalization-failures.md)。
