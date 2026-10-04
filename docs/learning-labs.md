# AI 应用开发实验与能力验收

本文件配合 [学习指南](learning-guide.md)和 [开发路线](../ROADMAP.md)。目标是能独立完成一个小功能、定位一次故障并解释验证证据。阅读过、看过演示、亲自完成是不同状态；目前不预填任何“已掌握”。

先在临时合成环境练习，不使用真实客户资料、在线模型密钥或日常业务数据库。环境和命令以相应实验及 [开发交接指南](../CONTRIBUTING.md)为准。在线模型实验在明确输入与费用上限后单独进行。

## 每个实验都完成五步

1. **运行**：记录提交、命令、输入和结果。必须能重新运行。
2. **解释**：画出页面、API、服务和数据之间的路径，解释其中一次状态变化。
3. **复现失败**：用固定输入触发缺项、冲突或故障，指出是哪层拒绝了操作。
4. **修改**：只改变一个规则或参数，先说明预期与可能退步的场景。
5. **验证**：证明改动生效，同时验证原来正确的路径没有被破坏；保留失败样本。

## 第一组实验

| 实验 | 对应业务问题 | 你应亲手完成的验证 | 代码入口 |
| --- | --- | --- | --- |
| 问答参数契约 | 有相关资料，却不含问题所需的参数 | 同型号的已知电压正常返回；缺价格、响应时间和复合缺项明确拒答；换库后范围正确 | `frontend/src/pages/RagLabPage.tsx`、`backend/app/agent_core/rag_service.py` |
| 引用与事实 | 引用存在，但引用没有支持所写结论 | 解释“编号有效”和“事实正确”的差别；在临时测试里修改型号或参数值，观察当前检查能否发现 | `backend/app/agent_core/fact_answer.py`、`tests/test_fact_answer.py`、`tests/test_required_facts.py` |
| 数据恢复 | 更新或故障后要找回资料和任务 | 使用实验专用库，停机备份、恢复到新目录，重新查询资料；损坏归档必须失败且不影响原库 | `scripts/local_backup.py`、[操作说明](local-backup.md) |
| 页面与审批 | 编辑通过的内容后，旧批准不能继续交付 | 实际操作生成、批准、下载、编辑、重新审核，核对两次交付的版本和引用 | `backend/app/services/review_lifecycle.py`、`backend/app/services/delivery.py` |
| 自动回归 | 测试通过是否覆盖用户真正操作 | 阅读新增浏览器用例，解释它如何隔离数据和网络、等待异步任务、检查下载结果 | `frontend/e2e/industrial-faq.spec.ts`、[运行说明](browser-regression.md) |

第一组先完成现有业务的可靠性。随后沿路线学习结构化输出、LangGraph 持久化与工具调用、模型追踪、数据库迁移和部署恢复。每个新增技术都要有触发它的业务问题、成本与失效方式说明。

## 第二组实验：评测与运营可靠性

| 实验 | 你应亲手完成的验证 | 入口 |
| --- | --- | --- |
| 冻结评测 | 运行 development 词项组，找到跨型号与复合缺项的误接收；解释误接收率的分母，区分保留组和真正盲测；不改题消除坏例 | [评测说明](frozen-faq-evaluation.md)、`scripts/evaluate_frozen_faq.py` |
| 请求关联 | 用合成任务取得响应请求 ID，在日志和 workflow attempt 中找到同一次执行；制造失败后确认没有记录正文和密钥 | [诊断说明](diagnostics.md)、`tests/test_diagnostics.py` |
| 人工对账 | 仅在临时控制库中建立预留，依次预览、执行、相同编号重试和不同输入冲突；注入审计失败确认事务完整回滚 | [对账说明](usage-reconciliation.md)、`tests/test_usage_reconciliation.py` |
| 容器升级回退 | 在有 Docker 的隔离环境构建基线与候选，比较升级前后审核 hash，恢复升级前快照后确认原卷未被覆盖；说明词项模式没有覆盖向量恢复 | [容器验收](container-acceptance.md)、`scripts/container_smoke.py` |

本轮提供可复现材料，不代表你已独立完成。真实模型实验仍需先明确供应商和预算；没有 Docker 的电脑可阅读 CI 证据，但不能登记为亲手完成本机容器演练。

## 第三组实验：问题范围、工具契约与未知结果

这组对应 v0.13.0 工程验证。继续执行前面的五步，不把代码存在、测试通过或他人演示填成自己的独立完成记录。

| 实验 | 你应亲手完成的验证 | 入口 |
| --- | --- | --- |
| 人工范围确认与上下文失效 | 从目录加入一个有证据的参数，再手填目录中没有的售价；未确认不能提交参数问答，确认后缺项明确返回。加入第二型号，验证没有被第一型号覆盖；依次修改问题、编辑/删除行、切换知识库，解释确认状态何时清除、旧结果为何不能继续用。清空清单后一般资料问答仍可用且完整性未评估 | [人工确认说明](question-clarification.md)、`RagFactCatalogPicker.tsx`、`RagLabPage.tsx`、`tests/test_fact_catalog.py` |
| 工具 schema 与契约透传 | 读取 `GET /api/v04/tools` 中 `rag.answer` 的 schema，分别经工具和直接接口提交同一组电压＋售价；核对工具 `output` 与直接响应的缺项一致。把 `required_facts` 拼成 `required_fact`，观察 422 并解释旧版静默忽略为何危险；指出 workspace/KB 应放在哪一层 | [工具事实要求](rag-tool-facts.md)、`backend/app/agent_core/tools.py`、`tests/test_rag_tool_facts.py` |
| 受理、传输与结算未知 | 在临时测试中分别制造明确 201、明确 422、提交副作用后的兜底 500，核对 `accepted/rejected/unknown`。注入结算提交前失败、提交后回执失败、后续审计或发送失败，计数证明每个预留最多一次自动结算尝试；已受理不反向退回，未知结果保留预留，取消后组织上下文恢复 | [结算故障说明](usage-finalization-failures.md)、[诊断事件](diagnostics.md)、`tests/test_usage_finalization_failures.py` |

运行专项测试使用对应说明中的 `configure_offline` 临时环境命令。若练习修改规则，记录修改前后结果并保留原失败样本；不能改冻结题、标签或把人工填写的正确清单注入自由问答评测后宣称误接收已解决。用量实验只处理请求次数，退回额度不是资金退款，单次自动尝试也不是外部服务 exactly-once。

## 第四组实验：运维预检与页面排错

| 实验 | 你应亲手完成的验证 | 入口 |
| --- | --- | --- |
| 磁盘读数和业务就绪 | 使用临时合成目录及显式阈值，依次观察 ok、low、unknown，再恢复原阈值；比较文件哈希与目录内容，确认检查没有写入。解释为什么 `/ready` 可读、容量足够、真正写入成功是三个不同结论；不要通过填满真实盘练习 | [磁盘预检](ops-health.md)、`scripts/ops_health.py`、`tests/test_ops_health.py` |
| 页面到日志的失败关联 | 在临时问答服务用超过 1000 字的问题触发真实 HTTP 422，比较响应头、页面编号和私有日志；复制只包含编号。拒绝剪贴板访问时手动选择，修改问题后旧编号消失。用请求层测试复现读取错误体时切换组织，确认旧错误被丢弃 | [排错编号](request-troubleshooting.md)、`frontend/src/api/client.ts`、`RequestFailure.tsx`、`frontend/tests/request-context.test.mjs` |

两项练习都不把本机验证等同于生产告警。磁盘阈值由操作者按真实增量选择，读数可能在下一次写入前变化；请求 ID 能关联已记录的事件，不能补齐丢失日志或证明账本状态。

## 第五组实验：双组织向量备份恢复

按 [SaaS 恢复演练](saas-recovery.md)先运行本机模式，有 Docker 时再独立运行容器模式。两种结果分开记录；脚本自己创建临时合成数据，不传入日常数据路径、账号或模型密钥。

| 实验 | 你应亲手完成的验证 | 入口 |
| --- | --- | --- |
| 存储与组织边界共同恢复 | 建立两个组织，解释为什么相同数字文档和 chunk ID 不能证明属于同一组织。停机快照后在原卷追加资料，恢复到新目标；重新登录核对两个组织的 semantic/hybrid 命中哈希、owner/viewer 权限和请求额度，证明原卷的新资料仍存在 | [九个检查点](saas-recovery.md#九个检查点)、`scripts/saas_recovery_scenario.py`、`tests/test_saas_recovery_scenario.py` |
| 失败门禁与独立证据 | 解释为什么尚无向量库的活跃服务仍应拒绝备份；找到归档哈希损坏、已有目标拒绝和原卷文件指纹对应的报告阶段。指出拦截发生在哪一层，并验证失败未发布或覆盖目标 | `scripts/saas_backup.py`、`backend/app/db/runtime_lock.py`、`scripts/saas_recovery_smoke.py` |

本机命令：

```bash
.venv/bin/python scripts/saas_recovery_smoke.py \
  --runtime local \
  --output .data/validation/saas-recovery/report.json
```

这里的 SQLite、Qdrant 文件和 HTTP 权限判断是真实实现，向量是明确标记的固定三维合成值。完成后应能解释：为什么检索恢复不等于 BGE 模型质量已验收；为什么丢弃客户端 Cookie 重新登录不等于恢复撤销了旧会话；为什么同一候选恢复不能证明跨版本降级。本机 Python socket 拦截也不等于操作系统网络隔离。按前面的五步独立完成再登记学习状态，不把脚本存在或他人报告当作自己已掌握。

## 第六组实验：私有诊断落盘与离线排错

这组对应 v0.16.0。先读 [诊断操作说明](diagnostics.md)，再运行只使用临时合成环境的演练；不用日常资料、账号或日志目录做故障注入：

```bash
.venv/bin/python scripts/diagnostic_smoke.py \
  --output .data/validation/v016/diagnostic-smoke.json
```

| 实验 | 你应亲手完成的验证 | 入口 |
| --- | --- | --- |
| 请求 ID 到保留文件 | 核对默认不落盘；明确配置私有目录后，取得响应头编号并找到 HTTP 与失败工作流事件。解释为什么 HTTP 受理不代表工作流成功、任意 `LOGGER` 消息为何不会自动进入白名单文件 | `backend/app/core/diagnostics.py`、`diagnostic_schema.py`、[诊断说明](diagnostics.md) |
| 轮转与未知历史 | 在合成环境用小容量轮转，查询已淘汰和最新 ID；核对退出码 0/1/2、`complete:false` 和 `truncated` 的区别。达到返回上限后放置坏行，确认仍返回不确定，并比较查询前后文件哈希保持不变 | `scripts/diagnostic_query.py`、`tests/test_diagnostic_query.py` |
| 写入失败与安全恢复 | 分别注入 write、fsync 失败，检查业务仍响应、日志停止继续写入且只提示固定降级码；第二实例不能抢占日志目录。保留坏文件、修复环境或换新私有目录后安全重启，不盲删锁或补造旧事件 | `backend/app/core/diagnostic_store.py`、`scripts/diagnostic_smoke.py`、[v0.16.0 验收](validation/v016-diagnostics-2026-10-04.md) |

本地九阶段通过是项目证据，学习状态仍由自己完成前面的五步后填写。写入 `fsync` 不等于断电后绝不丢失；`not_observed` 不等于没有故障，找到记录也不能直接推导额度结算或供应商费用。这里只提供可选单进程落盘与离线查询，没有自动常驻监控、远程采集或通知告警。

## 第七组实验：结构化问题提案与独立评测

这组对应 v0.17.0，先读[问题提案说明](question-scope-proposals.md)。使用合成资料及默认规则方式，不需要在线模型凭证。

| 实验 | 你应亲手完成的验证 | 入口 |
| --- | --- | --- |
| 原问题到人工确认 | 提问已知电压和未知售价，查看原文位置并逐项加入清单；未确认不能提交，确认后缺售价应拒答。分别输入未知型号和二选一，解释为什么目录不能删减问题要求；切换知识库后验证旧提案消失 | `QuestionScopeProposal.tsx`、`questionScope.ts`、`RagLabPage.tsx` |
| 结构有效与语义正确 | 在测试中返回越界位置、emoji 错位、额外字段或错型号，观察 502；再返回位置合法但漏参数的结果，解释为什么字段校验不能证明理解完整。比较规则和模型调用记录，不把协议 mock 当成真实质量 | `backend/app/agent_core/question_scope.py`、`schemas/question_scope.py`、`tests/test_question_scope.py` |
| 推理与评分隔离 | 运行新题集，核对原始 query 是唯一内容输入、标注在推理后读取；找到超出有限语法的坏例。解释明确题和澄清题指标各自的分母，以及“评测完成”和“质量通过”区别 | `scripts/evaluate_question_scope.py`、`scripts/fixtures/question_scope_v1/`、`tests/test_question_scope_evaluation.py` |
| 受理额度与上游费用 | 模拟供应商已生成但 schema 失败，确认内部额度退回而供应商可能仍计费；禁用组织连接后缓存 client 也不能调用。解释请求取消为何不等于取消上游执行 | `tests/test_question_scope_api.py`、`backend/app/saas/middleware.py` |

亲自增加一条未见过的合成问句，先写预期，再运行并记录结果。不要改冻结题或标注来消除失败；若用新题改规则，这条题就成为开发样本。真实模型实验要另记录供应商、允许发送内容、调用上限与费用证据，不能直接套用本地规则得分。

## 学习记录模板

复制以下字段到自己的私有笔记或练习记录；未完成的保持空白。不要填写客户资料、密钥、账号或完整模型正文。

```text
实验名称：
代码提交与日期：
使用的合成输入：
执行命令或页面步骤：
观察到的结果与证据位置：
页面 → API → 服务 → 数据路径：
复现的失败及原因：
我修改的规则：
修改前后的结果：
原功能的回归结果：
仍解释不清的问题：
状态：未开始 / 已阅读 / 已复现 / 独立修改并验证
```

## 能力验收

能解释以下问题，并通过代码或实验举证，才把对应能力标为完成：

- 为什么检索相关不能直接推出答案正确？证据缺项在哪一层决定，前端能否绕过？
- 事务回滚、任务重试和外部模型重复计费分别是什么问题？哪一项当前仍不能保证？
- 为什么备份数据库文件和完整恢复应用是不同验证？向量索引与模型配置如何匹配？
- 谁能批准内容？修改后哪一个 hash 变化，为什么必须重新审核？
- 自动测试使用了哪些模拟？哪些结论仍需要真实模型、目标服务器或客户参与？
- 为什么勾选参数范围只代表用户确认，而不是机器已经理解了全部问题？接口如何保留资料中没有的要求？
- 为什么最终 HTTP 503 不一定说明业务未受理？结算回执失败时，凭什么不能再次退款或重放任务？
- 为什么双组织恢复要同时比较引用 ID 和内容哈希、验证越权拒绝，并再次读取原卷新增资料？控制库恢复后旧会话是否一定失效？
- 为什么日志查询没有命中或只返回部分记录时不能断言业务没有发生？轮转、写入失败和返回数量上限分别影响什么证据？

每轮保留一个你独立完成的小改动及其说明。面试和项目介绍使用已亲自验证的范围；对仍需借助文档或帮助才能完成的部分如实说明。
