# 知源开发与交接指南

先读 [项目状态](docs/project-status.md)，按 [SETUP](SETUP.md) 准备 Python 3.12+ 和 Node.js 22，再用 [工业 FAQ 演示](docs/demo-walkthrough.md) 理解完整业务。后续改动按 [开发路线](ROADMAP.md)的验收标准推进；学习者按 [实验记录](docs/learning-labs.md)复现实际变更。当前范围是单机小团队试点；不要把本地测试通过写成生产或客户验收通过。

## 开始改动前

在项目根目录检查当前分支和未提交内容：

```bash
git status --short
git branch --show-current
git remote -v
```

`main` 是整合入口，后续开发分支为 `codex/content-agent`；独立任务可使用 `codex/<简短任务名>`。本机目录仍叫 `ai-content-agent` 不影响使用。已有改动先辨认归属，不用 reset、clean 或覆盖文件来获得“干净环境”。

保留 `.env`、`.env.saas` 和 `.data/`，示例配置只在对应文件不存在时复制。不要把密钥、数据库、客户资料、带账号的截图或模型正文日志提交到 Git。旧环境的演示库不能当测试库，团队与本地数据也不能混用。本地模式按 [本地备份恢复](docs/local-backup.md)操作；团队模式按 [SaaS 运维](docs/saas-operations.md)操作。

日常本地入口是 `./scripts/start.sh`（8765），可选团队入口是 `./scripts/start_saas.sh`（8766）；都以单服务进程运行。前端由后端提供构建产物，修改页面后需重新构建。同一数据目录只由一个服务使用；本地文件 SQLite 的启动锁用于避免同机重复服务误恢复任务，不是多机调度或分布式锁。

## 按业务找代码

| 业务 | 主要入口 | 优先回归的边界 |
| --- | --- | --- |
| 导航与页面上下文 | `frontend/src/App.tsx`、`components/AppShell.tsx`、`utils/navigation.ts`、`api/client.ts` | 稿件、任务、品牌、知识库编号不能丢失；组织切换不显示旧请求结果 |
| 品牌与模板 | `backend/app/api/routes/brands.py`、`models/brand_profile.py` | 品牌版本、任务快照、禁用表述、知识库和生成策略 |
| 导入与检索 | `api/routes/knowledge.py`、`services/document_parser.py`、`agent_core/` | 预览后确认、范围过滤、索引更新、候选和实际引用分别评测 |
| 核验与产品事实 | `services/evidence_notes.py`、`agent_core/evidence_policy.py` | 授权声明、有效期、撤销、同范围显式参数冲突与必需事实 |
| 任务与恢复 | `api/routes/agent_runs.py`、`services/content_growth_agent.py`、`services/workflow_support.py` | 节点事务、重试输入、取消、已完成步骤复用 |
| 审核与交付 | `services/review_lifecycle.py`、`services/delivery.py`、`frontend/src/pages/DraftEditorPage.tsx` | 编辑后批准失效、旧内容快照、当前证据、正式清单和预览素材的区别 |
| 模型调用 | `backend/app/llm/`、`api/routes/models.py` | 调用关联、JSON 修复、失败日志、未知 token/费用、不返回敏感原文 |
| 团队与复盘 | `backend/app/saas/`、`services/pilot.py`、`frontend/src/pages/PilotPage.tsx` | 组织边界、角色权限、有效交付版本、缺失值与真实零值 |

表中省略前缀的前端路径相对 `frontend/src/`，后端路径相对 `backend/app/`。测试集中于 `tests/`，前端行为测试在 `frontend/tests/`。先读相关测试再改动，不为已有能力重建第二套实现。

## 本地检查

完整检查入口：

```bash
./scripts/check.sh
```

脚本禁用 dotenv、隔离 SQLite 与词项模式、强制 local 任务模型，清除继承的模型凭证/价格并关闭信源和外部 LangSmith/LangChain 追踪，运行后端测试、Python 编译、前端行为测试和生产构建。不要把它的通过解释成真实语义模型、在线供应商、浏览器画面或生产环境已经验收。

开发期间可先跑相关用例。例如只改审核与交付：

```bash
PYTHON_DOTENV_DISABLED=1 SAAS_MODE=false DATABASE_URL=sqlite:///:memory: \
  RAG_RETRIEVAL_MODE=lexical DEFAULT_LLM_PROVIDER=local \
  DEFAULT_LLM_MODEL=local-rule-based-v0 PYTHONPATH=backend \
  .venv/bin/python -m pytest tests/test_delivery.py tests/test_workflow_states.py -q
```

该命令仅禁用 dotenv，不清除 shell 已导出的凭证；完整隔离入口优先使用 `check.sh`。需要改用真实模型的测试另行确认，不把凭证带入故障注入用例。

前端改动可运行：

```bash
(cd frontend && npm run test:request && npm run test:workflow && npm run build)
```

涉及页面业务流程时，运行 [真实浏览器回归](docs/browser-regression.md)：首次安装 Chromium 后执行 `(cd frontend && npm run test:e2e)`。它启动自己的临时合成服务，不能复用日常资料库。CI 也执行该流程并保存合成报告，失败时保留 trace 和截图。

新行为应补能抓住原问题的测试；状态、权限或范围变化同时检查拒绝路径。纯文档修改核对命令、链接和实际页面文案即可。GitHub `Project checks` 执行检查与构建，不负责部署；实际结果应附当前运行记录，不照抄历史测试数。

人工验证优先用 [工业 FAQ 临时环境](docs/demo-walkthrough.md)。评测脚本有不同副作用：先看 `--help` 和代码确认数据、网络、模型与报告位置，不默认所有评测都离线或只读。语义评测可能下载模型；真实在线调用需明确资料范围与费用预算，并单独记录结果。

## 交付一个改动

提交或交接说明至少包含：

- **问题与复现**：触发条件、原行为、用户影响，以及期望行为。
- **实际修改**：涉及哪些业务入口；是否改变 API、状态、数据结构或启动方式。
- **验证证据**：日期、提交或工作区状态、命令与结果；页面改动附实际操作路径。没有执行的检查明确标注。
- **剩余限制**：合成/模拟与真实运行分别说明；保留已知失败样本和恢复方法。

代码行为变化时同步当前指南；历史验收记录保留原结论，新结果使用独立记录。导航与阅读路线从 [docs/README.md](docs/README.md) 维护。新增交付能力仍需分别验证人工批准、客户接受和真实效果，不能互相代替。
