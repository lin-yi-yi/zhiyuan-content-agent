# 请求与工作流诊断

HTTP 响应带服务端生成的 `X-Request-ID`。创建任务或重试时，相同 ID 写入该次 `result_json.workflow.request_id`，并传入后台执行。现有模型记录通过 `agent_run_id`、`workflow_attempt`、`agent_step_id` 和 `step_key` 关联；本次没有新增模型日志表或复制提示词。

这是 R08 的关联基础，v0.13.0 增补用量结算失败和受理未知的诊断，v0.14.0 增加资料问答页的可复制排错编号与独立磁盘预检。它们不是完整告警平台。诊断默认输出到服务进程标准错误，每行一个 JSON 对象。

## 定位一次失败

1. 资料问答请求失败时，优先使用页面「复制排错编号」；没有编号的其他页面可从浏览器开发者工具的响应头复制 `X-Request-ID`。未知 HTTP 500 的响应 JSON 也包含它，但前端只采信响应头。客户端传入的同名头不会被采用。详见 [页面排错说明](request-troubleshooting.md)。
2. 查询 `GET /api/agent-runs/{run_id}`，核对 `result_json.workflow.request_id`、`attempt`、任务状态及受控失败代码。团队模式继续使用原有登录和组织权限。
3. 在私有服务日志中按这个 ID 查找 `workflow_step_failed` 或 `workflow_background_failed`，定位步骤与尝试次数，再查询 `GET /api/agent-runs/{run_id}/model-runs`。

重试获得新请求 ID，旧 ID 随原 attempt 保留在 `workflow.history`，继续沿用现有最多 20 次历史的限制。旧任务、直接服务函数调用或未经过请求入口的操作可能没有 ID，不按时间猜测或补造关联。

团队用量账本的新 `UsageEvent.request_hash` 是相同可信请求 ID 的 SHA-256。可用它与私有运维工具对照；旧事件不补造归属。用量请求被接受、后台任务成功和供应商实际收费是不同状态，不能互相推断。

## 事件和字段

| 事件 | 含义 |
| --- | --- |
| `http_response` | 响应头开始发送时的状态与耗时，覆盖团队提前拒绝；不表示后台任务已成功或下载完整送达 |
| `http_failed` / `http_failed_after_response` | 未处理异常发生在响应开始之前或之后；已发送的响应不能替换 |
| `saas_boundary_failed` | 团队边界出现内部失败并返回 503 |
| `usage_finalization_failed` | 自动结算调用未确认成功；代码为 `USAGE_FINALIZATION_UNCONFIRMED`，保留原业务受理结果，不表示数据库一定仍为 `reserved` |
| `usage_outcome_unknown` | 没有可确认的业务受理结果，未发起自动结算；代码为 `USAGE_OUTCOME_UNKNOWN`，保留预留待核查 |
| `workflow_started` / `workflow_finished` | 已取得执行权的尝试开始及结束，结束事件含状态和耗时 |
| `workflow_step_failed` | 节点执行失败；在回滚前记录已取得的步骤 ID，避免事务故障丢失关联 |
| `workflow_background_failed` | 后台出现未能按常规保存的失败，例如无法建立数据库会话；未知 attempt 保持空值 |

允许字段只有事件名、时间、请求 ID、已验证的组织 ID、任务和步骤 ID、尝试次数、固定步骤/状态/错误分类、耗时、HTTP 状态、方法、注册路由模板及 `usage_outcome`。鉴权前无法确认组织时为 `null`；未匹配路由为 `<unmatched>`。动态异常类名归入 `UnexpectedError`。

`usage_outcome` 仅允许 `accepted`、`rejected`、`unknown`，不适用或没有该上下文时为 `null`，任意字符串不会直接进入日志。它记录业务受理判断，不是响应传输成功或结算已提交的证明：首次明确业务响应小于 400 为 `accepted`，明确业务拒绝且 HTTP 状态大于等于 400 为 `rejected`，缺少业务响应或内部错误边界合成兜底响应为 `unknown`。例如业务已受理，后续审计失败导致最终 HTTP 503，相关事件仍可能携带 `accepted`；不能据此反向退回额度或盲目重放请求。

两种新事件以 warning 级别尽力输出，沿用请求和组织关联，不记录内部结算 token。每次预留最多自动尝试一次结算，结算回执失败可能已在数据库提交；未知预留须检查实际账本和业务副作用。完整故障语义及核对步骤见 [用量结算失败与未知受理](usage-finalization-failures.md)。

事件不采集请求体、prompt、资料正文、参数、查询串、原始 URL、Cookie、密钥、供应商返回内容、异常原文或堆栈。日志是私有运维资料；不要导出整份任务 JSON 代替脱敏事件，因为任务本身仍包含业务内容。

默认关闭 Uvicorn 原始 access log，改由上述事件记录 HTTP 状态。`scripts/start.sh`、`scripts/start_saas.sh` 和直接 `uvicorn app.main:app` 都经过相同应用配置；生命周期日志保留。反向代理、平台、第三方库及自定义日志配置需单独核对，不由本模块保证脱敏。

## 故障验证

安装依赖后运行离线检查：

```bash
.venv/bin/python - <<'PY'
import sys, tempfile
sys.path.insert(0, 'scripts')
from evaluate_industrial_faq import configure_offline
with tempfile.TemporaryDirectory(prefix='diagnostics-tests-') as folder:
    configure_offline(folder)
    sys.path.insert(0, 'backend')
    import pytest
    raise SystemExit(pytest.main([
        'tests/test_diagnostics.py', 'tests/test_usage_finalization_failures.py', '-q',
    ]))
PY
```

测试用临时数据库和合成资料，覆盖 401/403/503/500、响应发送后异常、节点事务失败、重试历史、真实团队数据库并发隔离、账本哈希关联、上下文清理及敏感标记负例。用量故障用例分别检查提交前失败、提交后回执失败、明确拒绝与兜底未知、审计和发送失败以及结算单次尝试。模型调用只使用 local；Uvicorn 入口检查只加载配置，不监听端口。引导函数在应用导入前禁用项目 `.env` 并配置临时环境，实际结果与范围见 [v0.13.0 工程验证](validation/v013-reliability-2026-10-04.md)。

## 尚未提供的保障

- 提供 [一次性磁盘余量预检](ops-health.md)与阈值处置步骤；没有持续监测、外部通知、真实磁盘写满故障恢复或生产告警演练。
- 模型日志写入失败、进程被杀和磁盘故障仍可能造成记录缺口；日志缺失不能视作零调用或零费用。
- 输出为尽力写入；诊断输出失败不会改变业务结果。没有远程采集、持久化审计保证、自动轮转或保留策略，需运行环境自行管理。
- 没有分布式追踪、后台队列、跨进程上下文传播或 exactly-once 保证。工作流中断仍沿用原有启动恢复与显式重试规则。
- 资料问答页提供排错编号复制；尚未覆盖全部页面，没有诊断包下载。编号、任务 API 和私有日志要按同一次请求核对。
