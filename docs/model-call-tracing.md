# 模型调用、重试与费用追溯

目标：在工业 FAQ 内容任务中，能检查某个步骤实际留下了哪些调用记录、哪次任务重试产生了它们，以及用量和费用有哪些未知。任务详情的「模型调用与费用」显示这些记录。

## 记录的含义

| 字段 | 含义 |
| --- | --- |
| `agent_run_id` / `agent_step_id` / `step_key` | 当前任务与 SQL 步骤；任务外独立模型调用保持未关联 |
| `workflow_attempt` | 工作流第几次尝试；显式重试后增加，前次记录保留 |
| `invocation_id` / `request_index` / `call_kind` | 一次逻辑调用的分组、实际请求序号、首次请求／格式回退／JSON 修复等原因 |
| `prompt_version` | 实际 system 指令的 SHA-256；指令改变则版本改变，不是人工发布的语义版本号 |
| `prompt_hash` | 完整 system/user 提示词的 hash；不会把原文存入调用日志 |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | 供应商返回的用量；没有返回时为 `null`，不按字数猜测 |
| `estimated_cost` / `cost_currency` / `pricing_version` | 根据显式配置的价格估算，金额以十进制定点字符串返回 |
| `error_type` / `success` / `latency_ms` | 脱敏异常类型、请求结果及耗时；不保留异常正文和请求正文 |

OpenAI 兼容 SDK 的内部自动重试设为 0，使每个 SDK 请求对应一个请求记录。现有有限 JSON 格式回退及修复仍会产生后续记录。同一逻辑调用的修复请求与用户发起的工作流重试分开计数；失败请求也可能被计费，不能把失败费用填为 0。

本地规则调用会标明 `provider=local`，不计为外部模型请求，token 和模型费用不适用。直接执行的本地摘录步骤未必经过模型客户端，因此调用记录数不等于流程步骤数。

旧数据库启动时补齐可空字段，不按时间猜测历史任务归属，也不伪造旧价格与用量。旧日志存在正文时，接口仍仅返回允许公开给当前组织成员的元数据。

## 价格配置

默认不提供市场价格。运营者核对自己的供应商、模型和适用价目版本后，可在私有环境配置 `MODEL_PRICING_JSON`。使用对象数组，按 `provider` 与 `model` 精确匹配：

```dotenv
# 以下仅演示字段，不是任何供应商的真实价格。
MODEL_PRICING_JSON='[{"provider":"example","model":"example-model","version":"example-price-v1","currency":"CNY","input_per_million":"1.00","output_per_million":"2.00"}]'
```

`估算 = (输入 token × 每百万输入价格 + 输出 token × 每百万输出价格) / 1,000,000`

价格必须非负且有限，重复匹配或非法配置不进行估算；显式的 0 价格仍是已知值。无价格、缺少用量、失败调用都保留未知，并通过 `cost_status` 区分原因。已写入的估算保留当时价格版本，不随未来配置重算。

当前只支持按输入／输出 token 的统一单价。缓存折扣、推理 token 单独计价、批量折扣、阶梯价、工具费和税费没有专门核算；若价目不符合这个模型，应留空并以供应商账单另行对账。供应商发票、人工、基础设施与支持费用未接入。

## 查询与核对

```text
GET /api/agent-runs/{run_id}/model-runs?limit=100&offset=0
GET /api/models/runs?limit=50
```

任务接口按当前组织数据库查询，返回分页记录和该任务所有已记录行的摘要。当前覆盖对话模型适配器，不包含 embedding 或其他外部工具。只有每条已记录外部请求都具有用量时才汇总 token；只有费用完整且币种一致时才汇总估算费用。部分已知金额不能冒充任务总费用，分页也不能改变摘要口径。

明细中的成功仅表示该请求得到响应，本次输出仍可能在 JSON 解析、证据核验或人工审核时不通过，应同时检查任务和步骤状态。

界面中的计数均是**成功写入的记录数**。进程崩溃、磁盘异常或日志写入失败可能留下缺口；没有关联记录不证明没发生调用或账单。当前没有持久化的请求发件箱、分布式执行或 exactly-once 计费保证，生产对账仍需供应商数据。

## 本地验证

```bash
./scripts/check.sh
PYTHON_DOTENV_DISABLED=1 DATABASE_URL=sqlite:///:memory: SAAS_MODE=false \
  RAG_RETRIEVAL_MODE=lexical PYTHONPATH=backend .venv/bin/python -m pytest \
  tests/test_model_tracing.py tests/test_agent_model_runs.py -q
```

测试使用合成数据和模拟供应商响应，覆盖关联、上下文隔离、失败与修复、显式工作流重试、可空费用和旧表迁移。离线模拟证明记录逻辑，不证明真实供应商返回用量、费用准确或客户已验收。

本轮实际结果见 [P1 本地验收记录](validation/p1-model-tracing-2026-10-03.md)。
