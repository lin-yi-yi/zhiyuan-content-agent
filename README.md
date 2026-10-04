# 知源 · AI 内容与交付 Agent

`zhiyuan-content-agent` · **v0.16.0 工程验证版** · RAG / LangGraph / 人工审核

[![Project checks](https://github.com/lin-yi-yi/zhiyuan-content-agent/actions/workflows/check.yml/badge.svg?branch=main)](https://github.com/lin-yi-yi/zhiyuan-content-agent/actions/workflows/check.yml?query=branch%3Amain)

将品牌资料整理成有依据、可修改、可审核的图文内容，完成 **品牌与资料 → 内容任务 → 人工审核 → 下载交付 → 效果复盘**。

面向专业内容团队和小型内容工作室，当前试点限于**一个品牌的工业产品 FAQ／售前答疑内容包**。保留原有三种模板；先把授权资料、事实、审核和交付这一条流程验证完整。求职练习放在项目文档中，产品前台继续服务内容工作。

默认本机免登录；团队模式提供账号、角色、组织隔离和用量控制。当前定位为**单机小团队试点**，软件功能不等于客户付费、生产合规或全平台自动发布已经验证。

## 当前版本与分支

| 项目 | 当前情况（2026-10-04） |
| --- | --- |
| 默认首页 / 整合主线 | [`main`](https://github.com/lin-yi-yi/zhiyuan-content-agent/tree/main)；默认显示已整合版本；开发分支的未合并改动通过 Pull Request 查看 |
| 后续开发分支 | [`codex/content-agent`](https://github.com/lin-yi-yi/zhiyuan-content-agent/tree/codex/content-agent)；由原 `codex/job-ready-agent-v06` 更名，避免把分支名中的 v06 误当当前版本 |
| 本轮实现 | 默认关闭的私有诊断 JSONL、限额轮转、按服务器请求 ID 只读查询；写入故障停止落盘并保留业务响应，损坏或读取不完整明确返回不确定状态 |
| 本轮验证 | **1441 项后端、57 项前端、编译构建及 1 条 Chromium 业务流程通过**，npm 已知漏洞 0；v0.16.0 真实本地子进程诊断演练 **9 阶段通过**，清理错误 0。候选 `beefa25` 的六项 CI 检查通过，见 [本轮验收](docs/validation/v016-diagnostics-2026-10-04.md) |
| 历史回归 | v0.15.0 的完整回归、浏览器与依赖审计结果保留在 [历史验收](docs/validation/v015-saas-recovery-2026-10-04.md)，不作为 v0.16.0 结果 |
| 本轮 Docker 演练 | 候选 `beefa25`：双组织 Qdrant 恢复 **9 阶段**、v0.15→v0.16 本地模式升级回退 **8 阶段**均通过，清理错误 0；[原始证据与范围](docs/validation/v016-diagnostics-2026-10-04.md) |
| GitHub 自动检查 | v0.16.0 候选 `beefa25` 的 push / PR 共 6 项检查成功；Linux 诊断九阶段、完整测试及两类容器恢复均通过。后续提交核对自身[运行记录](https://github.com/lin-yi-yi/zhiyuan-content-agent/actions/workflows/check.yml)。顶部徽章显示 `main` 状态，不能替代未合并候选的检查 |
| 真实业务验证 | 本轮使用临时合成资料与故障注入，无付费模型或外部通知；真实供应商账单、客户验收、付费和公网生产环境尚未验证 |

**[开发路线与商用验收](ROADMAP.md) · [学习实验](docs/learning-labs.md) · [项目状态与下一步](docs/project-status.md) · [安装与运行](SETUP.md) · [演示流程](docs/demo-walkthrough.md) · [文档导航](docs/README.md) · [开发交接](CONTRIBUTING.md) · [版本记录](CHANGELOG.md)**

## 启动与轻量运行

需要 **Python 3.12+、Node.js 22**。首次安装和下载中文向量模型需要网络，无需先安装 MySQL、Docker 或本地大语言模型。

```bash
./scripts/setup.sh
./scripts/start.sh
```

打开 **[本地工作台](http://127.0.0.1:8765)**。脚本构建前端，绑定本机地址，运行单个服务进程；按 Ctrl+C 停止。业务库、向量索引和模型缓存位于 `.data/`。

第一次使用：

1. 进入「品牌与资料 → 知识资料」，导入自己有权使用的资料，核对预览后确认入库。
2. 在「品牌档案」设置受众、语气、禁用表述和行动引导，绑定知识库。
3. 在「内容任务」选择品牌和三种模板之一，填写目标，生成草稿。
4. 编辑关联稿件与卡片，回到任务完成审核。通过后下载审核交付清单；图片在编辑器另行导出。
5. 在官方平台人工发布，按实际结果登记链接、时间和效果。

若暂时无法下载语义模型，可显式使用词项检索：

```bash
RAG_RETRIEVAL_MODE=lexical ./scripts/start.sh
```

语义检索失败不会静默降级。词项模式切回语义模式需要重建语义索引；已有同模型语义索引可用于 semantic/hybrid 两种模式。M1/8 GB 等轻量设备建议使用单服务进程、中文向量模型和按需在线生成，避免同时运行大型本地模型或第二套编排平台。

## 五个业务入口

| 入口 | 主要任务 |
| --- | --- |
| 工作台 | 开始任务，查看最近内容与待处理事项 |
| 品牌与资料 | 品牌档案、知识资料、发现信源；核验笔记和原始素材放在「更多」 |
| 内容任务 | 固定模板创作，查看进度、依据和审核结果；也可进行资料问答 |
| 审核交付 | 编辑正文、卡片，检查并导出素材 |
| 效果复盘 | 人工登记发布与累计指标，查看报告 |

模型与信源、运行诊断进入「设置与连接」；团队模式还提供成员、用量和账号。模型评分和编辑器检查清单不能代替人工批准。

## v0.16.0 当前能力

| 能力 | 实现与边界 |
| --- | --- |
| 品牌档案 | 受众、语气、禁用表述、行动引导、知识库和生成策略；版本号与并发修改检查，可归档 |
| 三种图文模板 | 专业知识图文、产品与服务答疑、真实案例整理；封装资料要求和生成步骤，必须使用知识库依据 |
| 品牌快照 | 创建任务时保存品牌与模板；后续改档案不影响旧任务；品牌表达不作为事实证据 |
| 禁用表述检查 | 批准和正式交付时，按每行配置做不区分大小写的字面包含检查；不是语义风险识别或广告合规审查 |
| 资料导入 | MD/TXT、文本 PDF、DOCX 预览后入库；支持编辑、去重、重建和删除索引 |
| 信源核验 | AIHOT REST、GitHub 信息、安全网页读取；核验笔记保留来源、引用、权利依据、版本和有效期 |
| 产品事实 | 核验引用可记录产品型号、参数、值及原文定位；任务可指定最多 10 项必需参数，缺依据则停止生成；同知识库相同产品参数的不同值须先处理冲突。仅做显式字段检查，不替代技术人员核对 |
| 资料问答 | 中文向量检索、知识库范围过滤、引用回答；可选 BM25＋向量＋RRF。从当前已核验目录选择或手填多个型号和参数，确认范围后提问，缺任一项依据则拒答；未指定时仍可普通问答并显示覆盖未评估 |
| 工具问答契约 | 白名单 `rag.answer` 与直接问答共用 `required_facts` 校验与事实门禁；该工具的未知参数返回 422，避免拼错或被静默忽略。目录和工具均不自动识别完整问题 |
| 参数声明核对 | 指定必需参数的在线问答只接受结构化声明，逐项核对型号、参数、值与 chunk；拒绝错值、遗漏及额外结论，成功回答由核验资料渲染。核对字段一致性，不验证资料真实性或未列要求 |
| 本地数据恢复 | 显式选择 SQLite 与 Qdrant 路径；停机快照、WAL、文件清单与哈希校验；只恢复到新或空目录。合成索引恢复后可查询，实际业务恢复需另验收 |
| 团队恢复演练 | 临时双组织、真实认证/权限/用量与 SQLite/Qdrant；新目标恢复后重新登录、检索并保留原卷新增资料。本机与候选 Docker 九阶段通过；固定三维合成向量不验证 BGE 质量或缓存，跨版本 SaaS 和生产验收仍待 |
| 内容流程 | LangGraph 条件流程：检索、选题、正文、卡片、质量检查和有限修订；步骤记录、重试和取消 |
| 调用追溯 | 模型调用关联任务、步骤及工作流尝试；区分格式回退和 JSON 修复，记录指令版本 hash、用量及配置价格下的估算。缺失留空，估算不等于供应商账单 |
| 请求诊断 | 服务器请求 ID 关联 HTTP、后台任务、执行尝试及新预留记录，页面可复制排错编号；可选私有 JSONL 轮转只写白名单元数据。标准库 CLI 按 ID 只读查询，损坏/变更不当作完整历史；写入失败降级停写，不改变业务结果。默认关闭，无通知告警或断电耐久保证 |
| 磁盘预检 | 显式目录和阈值的只读 CLI，区分正常、低空间与未知；不读取数据库、不清理文件。是一次检查，不是持续告警、写入保证或容量验收 |
| 异常预留核对 | 本地运营 CLI 默认预览，显式停机后处理单条已查明预留；幂等键、账本与审计同事务。只处理受理次数，不处理资金或供应商退款 |
| 结算故障保护 | 首次业务受理/拒绝只尝试一次结算；兜底 500、取消或无响应保留未知预留，审计或发送故障不反向退款；日志记录未确认状态，交由人工核对 |
| 冻结评测 | 20 题、14 份合成资料、开发/保留组、文件与配置哈希、坏例与分母；真实缓存 Embedding 可离线运行。标签待人工复核，不是独立盲测或生成模型质量验收 |
| 审核版本 | 退回原稿修改并重新送审；编辑已批准的正文/卡片会使旧批准失效，保留审核及编辑前内容快照 |
| 正式交付清单 | 服务端检查批准状态、正文/卡片 hash、当前引用有效性；通过后生成含正文、出处、品牌快照、审核版本及人工发布检查的 Markdown |
| 素材与复盘 | Canvas 卡片预览、PNG/ZIP 下载；手工发布记录、累计效果数据和报告 |
| 试点验收记录 | 每任务一条可更新的人工记录，绑定有效交付版本；未采集工时留空，旧版本验收失效。不是完整事件历史、付款记录或 ROI |

**交付清单与图片素材分别下载。** 编辑器素材下载不代表已批准或已发布；正式清单需通过服务端核验。清单不导出审核人账号、内部审核备注、原始引用摘录或模型配置。已审核正文仍原样交付，不提供通用个人信息自动脱敏。

“品牌任务仅本地生成”只约束该品牌创作任务的模型选择。本地生成是规则整理和原文摘录，**不是本地大语言模型**；该设置也不是整个知识库、问答接口或网络访问的统一防外发策略。

文档单文件上限 **1 MiB**、PDF 最多 **50 页**、提取正文 **40～120,000 字符**；解析有独立进程、超时及并发限制。扫描 PDF 的 OCR 未支持；混合 PDF 提示未提取页。DOCX 不提取图片文字、批注、页眉页脚，不保证复杂表格顺序。预览不等于已入库。

混合检索使用应用层 BM25 和 RRF，不是交叉编码器重排。默认语义模式；问答页选择不会修改其他任务配置。不同策略各有坏例，不能把融合等同于整体准确率提升，详见 [检索实验与限制](docs/research/v08-hybrid-retrieval.md)。

## 技术栈

| 层级 | 当前采用 |
| --- | --- |
| 页面 | React 18、TypeScript、Vite、CSS；请求取消与上下文隔离 |
| 接口与数据 | Python、FastAPI、Pydantic、SQLAlchemy 2、SQLite WAL |
| 检索 | LangChain 文档与递归切块、FastEmbed、`BAAI/bge-small-zh-v1.5`、Qdrant Local、BM25、RRF |
| 编排与审核 | LangGraph StateGraph、SQL 步骤持久化、人工审核、内容快照 hash |
| 模型与信源 | 兼容 OpenAI 接口的模型路由、HTTPX、REST 适配 |
| 文档与导出 | pypdf、DOCX ZIP/XML、Canvas、JSZip、FileSaver |
| 团队与验证 | Cookie 会话、CSRF、角色权限、组织独立数据库/向量目录；pytest、前端行为测试、Playwright Chromium 业务回归、构建检查 |

默认中文模型在 CPU 上生成 512 维向量。在线生成适配 DeepSeek、千问、豆包和 Kimi，需配置并验证服务。步骤记录采用 SQL，未实现 LangGraph 原生 checkpointer 或分布式任务；外部 MCP 配置不等于自建 MCP 服务。

```text
frontend/                     业务界面、请求与卡片导出
backend/app/api/routes/       品牌、内容、交付等接口
backend/app/services/         简报解析、内容流程、审核与交付
backend/app/agent_core/       切块、向量、混合检索与证据策略
backend/app/saas/             身份、组织、权限、额度与审计
scripts/                      安装、启动、检查、评测与备份
tests/                        临时数据上的行为与隔离测试
docs/                         操作、研究、历史与验收记录
```

## 在线模型配置

本地模式读取 `.env`。没有文件时参考 `.env.example` 创建；已有文件仅修改需要的字段，保留凭证。例如：

```text
DEEPSEEK_API_KEY=你的凭证
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

重启后在「设置与连接 → 模型与信源」测试，再在任务或问答中选择。地址和模型名以账户实际配置为准。任务和检索片段会发送给所选服务；国内 API 不等于资料不出企业或完全离线。

可用 `PORT=8770 ./scripts/start.sh` 调整端口。日志记录调用哈希、耗时、token 和错误类型；接口不返回历史提示词和回答原文，旧库原文记录不会自动清除。调用配额不是精确人民币账单，费用需与供应商对账。

## 可选：私有诊断与按编号排错

诊断默认仍输出到进程标准错误，**文件落盘默认关闭**。启用时显式设置 `DIAGNOSTIC_LOG_DIR` 为绝对路径，指向当前用户拥有、无组或其他用户权限的现有独立目录；新目录须为空，日志文件权限为 `0600`。默认每文件最多 1 MiB、保留 5 个旧档和 1 个活动文件；权限、格式或占用不符时拒绝启动，不接管或清理其他文件。

文件只保存版本化的事件、编号、状态、耗时等允许字段，不保存提示词、资料正文、密钥、原始 URL 或异常正文。可用 `scripts/diagnostic_query.py --directory ... --request-id ...` 只读查询；它不获取写锁、不修复日志。查到记录也不代表历史完整，轮转后查不到只表示保留窗口内未观察到，损坏或读取中变更返回不确定状态。启用、退出码和处置步骤见 [请求诊断指南](docs/diagnostics.md)。R08 仍部分完成，尚无持续监测或外部告警。

## 可选：SaaS 小团队试点

没有 `.env.saas` 时根据 `.env.saas.example` 创建，不要覆盖已有凭证。

```bash
(cd frontend && npm run build)
./scripts/start_saas.sh
```

打开 **[团队试点入口](http://127.0.0.1:8766)**。示例配置允许创建账号与组织。团队数据位于 `.data/saas`，不覆盖或自动导入本地 `.data/demo.db`。

组织所有者、编辑、审核人员和只读成员的权限在服务器校验；组织拥有独立业务库与向量目录。同组织品牌绑定知识库，但**没有品牌级成员访问权限**，品牌档案不能替代独立客户租户。

模型凭证由运营环境保管，组织所有者选择启用连接。AIHOT 的 SaaS 使用需要来源方商业授权，默认关闭。外部来源、图片、字体和模型服务按实际授权使用。

脚本仅监听 `127.0.0.1`。应用 lifespan 在初始化前持有 SaaS 数据目录锁，标准脚本、直接 Uvicorn 和 Docker 共用；它只能协调遵守协议的单机进程。[双组织 Qdrant 恢复演练](docs/saas-recovery.md)已完成本机和候选 Docker 合成验收；[容器升级回退](docs/container-acceptance.md)另行验证特定版本对与词项流程。跨版本 SaaS、真实模型缓存、Compose、HTTPS、公网部署和生产验收仍需分别完成。GitHub CI 不执行部署。详见 [SaaS 运维指南](docs/saas-operations.md)。

## 商业化尚未完成的部分

- 渠道专属适配、规则自动更新、多平台自动发布和效果自动采集。
- 任务交期、指定负责人、内容日历、免账号外部客户审批。
- 品牌级访问权限、分布式队列、生产扩容和服务等级承诺。
- 真实支付结算、发票、邮件验证、密码找回及 MFA。
- OCR、完整视频生产、飞书/企业微信接入、Obsidian 双向同步。
- 公网生产验收、完整 AI 标识符合性检测及其他适用上线义务核验。
- 真实客户付费、复购和业务效果验证。

AI 辅助说明和交付清单不构成完整生产合规证明。服务中断后的任务标记失败，显式重试继续；已发送的模型请求不能撤回，重试可能再次计费。同一 Qdrant Local 数据目录目前仅允许单进程使用。

## 检查与产品依据

```bash
./scripts/check.sh

# 独立临时数据中比较检索策略，不调用在线生成模型
.venv/bin/python scripts/evaluate_hybrid_retrieval.py

# 冻结开发组，保留误接收等坏例；不调用在线生成模型
.venv/bin/python scripts/evaluate_frozen_faq.py

# 临时双组织与真实 Qdrant 恢复；使用合成向量，不调用在线模型
.venv/bin/python scripts/saas_recovery_smoke.py --runtime local

# 临时子进程与私有目录：诊断查询、重启、轮转、写入/fsync故障
.venv/bin/python scripts/diagnostic_smoke.py
```

检查脚本覆盖后端行为、Python 编译、前端行为与生产构建。另用 `(cd frontend && npm run test:e2e)` 运行隔离的浏览器业务回归，首次先按 [浏览器回归说明](docs/browser-regression.md) 安装 Chromium。本地数据保护见 [备份恢复](docs/local-backup.md)。自动检查、浏览器验收和真实客户效果分别记录，不能互相替代。

本轮工程说明：[私有轮转诊断与只读查询](docs/diagnostics.md)、[v0.16.0 验收进度](docs/validation/v016-diagnostics-2026-10-04.md)。已有基础：[双组织 SaaS 与 Qdrant 恢复](docs/saas-recovery.md)、[学习实验](docs/learning-labs.md)、[磁盘余量预检](docs/ops-health.md)、[页面排错编号](docs/request-troubleshooting.md)、[人工确认问题范围](docs/question-clarification.md)、[工具问答契约](docs/rag-tool-facts.md)、[结算故障与未知状态](docs/usage-finalization-failures.md)、[冻结评测](docs/frozen-faq-evaluation.md)、[人工用量核对](docs/usage-reconciliation.md)、[容器升级回退](docs/container-acceptance.md)。冻结自由问答坏例仍然存在；人工确认和严格参数契约不能替代自由问题理解，也不能泛化为所有内容均已核验。

- [v0.9 商业化计划与价格实验](docs/v09-commercialization-plan.md)
- [v0.10 工业 FAQ 审计、实现与本地验收](docs/validation/p0-industrial-faq-2026-10-03.md)
- [模型调用、重试与费用追溯](docs/model-call-tracing.md)
- [本人可运行的工业 FAQ 练习](docs/industrial-faq-practice.md)
- [v0.10 有边界的客户试点计划](docs/v010-pilot-plan.md)
- [中国场景、平台边界与两周试点](docs/research/v09-china-content-market.md)
- [主流产品与开源工作流参照](docs/research/v09-content-workflow-benchmarks.md)
- [信源核验与知识入库](docs/source-evidence-workflow.md)
- [AIHOT、官方 MCP 与商业使用边界](docs/aihot-source-integration.md)
- [v0.9 实际开发与验收记录](docs/validation/v09-commercial-workflow-acceptance.md)
- [历史：v0.8 验收记录](docs/validation/v08-workbench-acceptance.md)

产品路线以实际交付任务和续费证据调整，不承诺爆款、涨粉或获客数量。
