# 请求与工作流诊断

HTTP 响应带服务端生成的 `X-Request-ID`。创建任务或重试时，相同 ID 写入该次 `result_json.workflow.request_id`，并传入后台执行。现有模型记录通过 `agent_run_id`、`workflow_attempt`、`agent_step_id` 和 `step_key` 关联；本次没有新增模型日志表或复制提示词。

这是 R08 的关联基础，v0.13.0 增补用量结算失败和受理未知的诊断，v0.14.0 增加资料问答页的排错编号与磁盘预检。v0.16.0 增加可选的私有轮转日志和按请求 ID 离线查询。默认仍只向服务进程标准错误输出事件 JSON，不落盘；它们不是持续监控或告警平台。

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

## 可选私有轮转日志

先选一个新的专用私有目录，由运行服务的当前用户创建并设为 `700`。下面使用家目录下的新目录；若它已经存在，先核对用途和内容，不要直接修改权限或混入其他文件：

```bash
mkdir "$HOME/.zhiyuan-diagnostics"
chmod 700 "$HOME/.zhiyuan-diagnostics"
DIAGNOSTIC_LOG_DIR="$HOME/.zhiyuan-diagnostics" \
DIAGNOSTIC_LOG_MAX_BYTES=1048576 \
DIAGNOSTIC_LOG_BACKUP_COUNT=5 \
./scripts/start.sh
```

团队模式可将同样的变量传给 `./scripts/start_saas.sh`；持久配置分别写入私有 `.env` 或 `.env.saas`，保留已有凭证。配置在应用 lifespan 启动时读取，修改后需安全重启。`DIAGNOSTIC_LOG_DIR` 未设置或为空时关闭落盘，不创建目录或日志文件。

| 配置 | 默认与允许范围 |
| --- | --- |
| `DIAGNOSTIC_LOG_DIR` | 默认空；必须显式提供已存在的绝对目录，属于当前 UID，无 group/other 权限，末级不能是符号链接 |
| `DIAGNOSTIC_LOG_MAX_BYTES` | 默认 `1048576`（1 MiB）；整数 `8192`～`16777216`，包含边界，限制每个日志文件 |
| `DIAGNOSTIC_LOG_BACKUP_COUNT` | 默认 `5`；整数 `1`～`20`，表示旧档份数，另有一个活动文件 |

文件是 `diagnostics.jsonl`、`diagnostics.jsonl.1` 至配置的旧档上限，另有 `diagnostics-format.json` 和 `.diagnostics.lock`；全部为当前 UID 所有的普通文件、权限 `600`，拒绝符号链接和硬链接。父级系统别名如 macOS 的 `/var` 可以存在，目录身份仍会检查。初次只接受空目录；重启只接受格式标记正确、结构和权限符合要求的已有目录。未知文件、已有超限文件或冲突的保留份数会阻止启动，不自动迁移或修复。

只有 `emit_diagnostic` 生成并通过固定 schema 校验的元数据会写入文件，记录增加 `schema_version: 1`，每行含换行最多 4096 字节。**不是通用 logging 文件处理器**：任意 `LOGGER.warning(...)`、第三方日志或异常堆栈不会自动进入这组文件，也不会因此得到自动脱敏。标准错误输出仍由原日志配置管理。

超过大小上限前先轮转，`.1` 是最近旧档，达到份数上限会删除最旧日志。默认最多保留 6 个数据文件，每个不超过 1 MiB；没有按时间保留或完整历史保证。日志目录由单进程持有文件锁，第二个应用写入者启动失败；锁不能阻止不遵守协议的外部程序。不要共享给多个 worker、不同应用实例或其他写入程序。

每条记录同步写入并执行 `fsync`，会增加请求或工作流耗时，目前没有吞吐量验收。私有权限和文件身份检查不提供同 UID 恶意篡改防护，也没有签名或防篡改审计保证。

## 只读查询一个请求 ID

用实际响应头中的 32 位小写十六进制 ID 替换示例值：

```bash
.venv/bin/python scripts/diagnostic_query.py \
  --directory "$HOME/.zhiyuan-diagnostics" \
  --request-id 0123456789abcdef0123456789abcdef \
  --limit 50
```

工具只依赖标准库和无副作用的 schema 模块，不加载 `.env`、应用入口或数据库，不访问网络，不创建目录、文件或锁。可在服务运行中查询，不要求停服。目录必须为绝对路径；`--limit` 默认 50，允许 1～200。最多扫描 21 个保留日志，每个最多 16 MiB，按旧档到活动文件、文件内行序返回最早的匹配记录；达到返回上限仍继续检查后面的记录。

| 退出码 | JSON 状态 | 含义 |
| --- | --- | --- |
| 0 | `scan_status: ok`、`match_status: found` | 保留文件扫描未发现缺陷且找到记录；仍不证明完整历史或业务成功 |
| 1 | `ok`、`not_observed` | 在本次保留窗口没观察到；可能已轮转淘汰或从未成功记录，不能解释成没有故障 |
| 2 | `partial` 或 `unavailable` | 参数、权限、文件变化、超限或记录损坏等造成不确定；即使命中也不能按完整扫描解释 |

所有结果的 `complete` 恒为 `false`。`records` 只含通过白名单校验的字段；`counts` 区分扫描、匹配、返回、无效行及观察到变化的项数，不是请求次数或费用。`truncated` 只表示匹配结果超过返回上限，不替代 `scan_status`；同 ID 多个事件不会自动去重。

读取时发生追加、轮转、替换或截断，或发现半行、过长行、重复 JSON 键、未知文件和错误 schema，返回固定 `reason_codes`，不回显路径、原始坏行或异常文本。日志缺口保持未知；活跃写入导致 `partial` 时可在较安静时重试。持有这些文件的本机运维用户能看到各组织的诊断元数据，它不是租户自助查询 API，也不通过请求 ID 授予权限。

## 失败与降级后的处置

启动时目录、权限、格式或锁不符合要求，会拒绝启用服务。运行中写入、`fsync` 或文件身份检查失败后，落盘进入 `degraded`，保留锁并停止继续写入；业务响应和请求 ID 仍按原流程工作。标准错误尽力输出一次固定 `diagnostic_sink_degraded` 提示，关闭文件失败也使用该提示；无效事件被拒时另有 `diagnostic_record_rejected` 提示，不保存原文。提示本身也可能输出失败，不是可靠告警投递。计数与状态是进程内诊断信息，没有新增公共健康或跨租户查询接口。

1. 先核对私有标准错误日志、磁盘余量、目录所有者及权限、是否有其他写入者；不要因日志失败盲目重放业务、退回额度或删除锁。
2. 正常停止持锁服务，保留原目录用于排查。修复已确认的环境问题；遇到半行、损坏或未知文件时保留原始证据，可另建空私有目录再显式改配置，不自动截断、清洗或拼接旧日志。
3. 安全重启后取得一个新请求 ID，并查询确认新事件已写入。写入或 `fsync` 失败时部分字节可能已经存在，不补写历史缺口，不把再次找到记录当作第一次持久化已成功。

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
        'tests/test_diagnostics.py', 'tests/test_usage_finalization_failures.py',
        'tests/test_diagnostic_store.py', 'tests/test_diagnostic_query.py',
        'tests/test_diagnostic_lifecycle.py', '-q',
    ]))
PY
```

测试使用临时数据，覆盖响应与工作流关联、用量故障、敏感标记、私有文件权限、轮转、活跃查询、路径替换及写入失败。引导函数在应用导入前禁用项目 `.env` 并配置临时环境。早期关联及用量结果保留在 [v0.13.0 工程验证](validation/v013-reliability-2026-10-04.md)。

实跑独立临时 Uvicorn 服务的演练另行执行，不使用操作者配置的日志目录：

```bash
.venv/bin/python scripts/diagnostic_smoke.py \
  --output .data/validation/v016/diagnostic-smoke.json
```

本地已通过九阶段：默认关闭、请求查询、已受理请求的工作流失败、重启保留记录、第二写入者拒绝、轮转淘汰、write 故障、fsync 故障、坏行查询保持不确定。报告及后续提交的实际检查见 [v0.16.0 验收](validation/v016-diagnostics-2026-10-04.md)。这是合成输入、定向故障注入和本地子进程验证；没有模型调用或外部通知，也不是实际磁盘写满、断电或生产容量验收。

## 尚未提供的保障

- 提供 [一次性磁盘余量预检](ops-health.md)与阈值处置步骤；没有持续监测、外部通知、真实磁盘写满故障恢复或生产告警演练。
- 模型日志写入失败、进程被杀和磁盘故障仍可能造成记录缺口；日志缺失不能视作零调用或零费用。
- 可选文件落盘有大小和份数轮转，但仍为尽力记录；写入失败不改变业务结果。没有远程采集、持久化审计保证、断电持久性或完整历史保证，日志不是账本真相来源。
- 没有分布式追踪、后台队列、跨进程上下文传播或 exactly-once 保证。工作流中断仍沿用原有启动恢复与显式重试规则。
- 资料问答页提供排错编号复制；尚未覆盖全部页面，没有诊断包下载。编号、任务 API 和私有日志要按同一次请求核对。
