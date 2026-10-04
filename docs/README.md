# 知源文档导航

当前使用与交接路线面向 **v0.15.0 工程验证版**。当前版本与开发基线、最近检查及剩余限制以 [项目状态](project-status.md) 为入口；历史材料保留各自日期与验证范围。

## 第一次使用

1. [项目首页](../README.md)：项目解决什么问题、有哪些边界。
2. [安装与运行](../SETUP.md)：macOS/Linux、WSL2、本地 SQLite 与可选团队环境。
3. [当前演示流程](demo-walkthrough.md)：用临时合成工业 FAQ 走通资料、任务、重审和下载。
4. [工业 FAQ 练习](industrial-faq-practice.md)：亲自运行、解释请求、复现坏例。

## 接管和开发

| 文档 | 何时阅读 |
| --- | --- |
| [开发交接指南](../CONTRIBUTING.md) | 改代码前：环境保护、模块入口、检查和交付记录 |
| [开发路线与商用验收](../ROADMAP.md) | 查看当前开发顺序、技术选型和进入下一阶段所需的证据 |
| [学习指南](learning-guide.md) | 从一次请求学习 RAG、状态机、事务、评测和排错 |
| [学习实验与能力验收](learning-labs.md) | 亲自运行、解释、复现故障、修改并验证，记录真实掌握程度 |
| [模型调用、重试与费用](model-call-tracing.md) | 核对调用关联、未知用量和费用估算口径 |
| [SaaS 运维](saas-operations.md) | 单机团队试点、备份恢复与运维约束 |
| [双组织 SaaS 与 Qdrant 恢复](saas-recovery.md) | 临时合成账号、真实存储与 HTTP 核验；九个恢复门禁、运行命令和隔离边界 |
| [本地备份恢复](local-backup.md) | 默认本地或明确指定的 SQLite 与 Qdrant 停机快照、恢复到新目录 |
| [浏览器业务回归](browser-regression.md) | 自动点击资料、任务、审核和下载；查看隔离条件与失败产物 |
| [冻结合成 FAQ 评测](frozen-faq-evaluation.md) | 固定问题、资料、配置和坏例，区分开发/保留组与人工标签限制 |
| [问题范围人工确认](question-clarification.md) | 从当前资料目录选择型号和参数，补充缺项并确认；了解与一般资料问答的区别 |
| [工具问答事实契约](rag-tool-facts.md) | 将显式参数透传给 `rag.answer`，处理未知字段由忽略改为 422 的兼容变更 |
| [请求与工作流诊断](diagnostics.md) | 从请求 ID 定位执行尝试和步骤；了解日志字段与未覆盖的告警 |
| [磁盘余量预检](ops-health.md) | 按明确目录和阈值检查可用空间，区分低空间与无法判断，避免自动删数据 |
| [页面排错编号](request-troubleshooting.md) | 在资料问答失败时复制编号，对照私有日志定位同一次请求 |
| [用量结算失败与未知结果](usage-finalization-failures.md) | 区分业务受理、响应传输和结算回执；核对单次自动尝试与未知预留 |
| [中断预留人工对账](usage-reconciliation.md) | 已查明请求状态后，预览并以幂等、带审计的方式处理单条额度 |
| [容器升级与快照回退](container-acceptance.md) | 构建明确的基线与候选版本，在隔离卷中验证完整交付和恢复 |
| [v0.10 试点计划](v010-pilot-plan.md) | 准备真实授权材料、人员责任和验收口径 |

当前产品入口为「工作台、品牌与资料、内容任务、审核交付、效果复盘」，设置位于侧栏底部。旧文档中的「信源台」对应「品牌与资料 → 发现信源」，「检索与评测」对应「内容任务 → 资料问答」。学习中心不再是当前产品入口；相关材料用于源码学习。

## 实现依据与历史证据

| 类别 | 入口与使用方式 |
| --- | --- |
| 当前阶段的验收证据 | [v0.15.0 SaaS 恢复](validation/v015-saas-recovery-2026-10-04.md)及[项目状态](project-status.md)：本机九阶段已过，1235 项后端、57 项前端及构建通过；Docker CI 待验证 |
| 近期历史证据 | [v0.14.0 运维与排错](validation/v014-operations-2026-10-04.md)、[P0 工业 FAQ](validation/p0-industrial-faq-2026-10-03.md)、[P1 模型追溯](validation/p1-model-tracing-2026-10-03.md) 保留当时提交、结果和限制 |
| 资料机制 | [信源核验与入库](source-evidence-workflow.md)、[AIHOT 接入](aihot-source-integration.md)：了解机制和授权边界；旧页面名称按上方映射阅读 |
| 检索研究 | [v0.8 混合检索实验](research/v08-hybrid-retrieval.md)：保留样本、策略取舍和坏例，不能作为当前整体准确率 |
| 历史验收 | [v0.8 工作台](validation/v08-workbench-acceptance.md)、[v0.9 商业流程](validation/v09-commercial-workflow-acceptance.md)、[团队试点](validation/saas-pilot-acceptance.md)：按记录中的时间、版本和环境理解 |
| 产品与市场研究 | [v0.9 商业化计划](v09-commercialization-plan.md)、[中国场景研究](research/v09-china-content-market.md)、[工作流参照](research/v09-content-workflow-benchmarks.md)：决策背景，不等于已实现或已获客 |
| 早期设计与学习 | `v0.4-agent-architecture.md`、`v0.5-data-loop.md`、`learning-center.md`、`history/`：保留演进语境，不作为当前操作指南 |

`validation/` 存验收记录和报告，`research/` 存带日期的研究，`history/` 存已归档资料。根目录中带旧版本号的设计文档暂留原位置，避免断开旧链接；无需批量搬迁或改写旧结论。

新增证据请写清日期、提交、输入、命令、实际结果与限制；用独立输出文件保留本轮结果。自动测试、浏览器验收、真实模型、客户接受和生产运行是不同证据层次。
