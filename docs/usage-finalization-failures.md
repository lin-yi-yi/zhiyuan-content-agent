# 用量结算失败与未知受理状态

团队额度计数按 HTTP 受理尝试计算。业务受理、响应是否送达、后台任务成功、模型 token 与供应商收费是不同事实。异常时不能把其中一个状态当作另一个状态的证据。

## 受理状态怎样确定

| `usage_outcome` | 依据 | 自动处理 |
| --- | --- | --- |
| `accepted` | 业务首次响应状态小于 400 | 尝试把预留转为 `settled` |
| `rejected` | 业务明确响应状态为 400 或以上，包括正常参数校验 422 | 尝试把预留转为 `refunded`，只退回尝试额度 |
| `unknown` | 没有业务响应、取消或断连；或内层错误边界合成兜底 500 | 保持预留待人工核对，不自动退款 |

内层错误边界只通过私有 ASGI 状态标记兜底响应，客户端头和 JSON 字段不能控制它。即使路由已经提交副作用再抛异常，兜底 500 也不会被误认成“业务明确拒绝”。

每次预留只按第一次确定的业务结果尝试一次结算。结算异常不改变已确定的业务响应，也不在清理阶段反向结算或自动重试；仍处于 `reserved` 的记录继续占用额度。后续审计、日志或网络发送失败不会把 `accepted` 改成 `rejected`。

结算可能已经提交，而调用方没有收到成功回执。因此诊断只说“未确认”，不保证数据库一定仍为 `reserved`。正常成功提交后抛错的记录仍保持原终态。请求取消、清理诊断自身异常也必须恢复租户上下文。

## 怎样发现与处置

新增两种 [脱敏诊断事件](diagnostics.md)，沿用可信请求 ID 和已验证组织 ID：

- `usage_finalization_failed` / `USAGE_FINALIZATION_UNCONFIRMED`：结算调用未确认成功；包含原业务 `usage_outcome`、HTTP 状态及固定错误分类。
- `usage_outcome_unknown` / `USAGE_OUTCOME_UNKNOWN`：没有足够证据确认受理结果，未调用自动结算。

相关 HTTP 和团队边界事件也携带 `usage_outcome`。例如审计失败造成最终响应 503，但该字段仍可能是 `accepted`。不要据此盲目重放业务：先核对任务及已提交的副作用。

这些事件不包含内部结算 token、请求体、资料、模型响应、Cookie、密钥、原始 URL、异常原文或堆栈。未知异常类名使用固定分类。

1. 先检查控制库可用性和私有服务日志，用请求 ID 与组织范围定位故障。
2. 用 `sha256(请求 ID)` 对照该组织用量记录。若已是终态，不重复处理，也不推断需要反向结算。
3. 若仍为 `reserved`，结合业务响应、任务和副作用核对证据，使用 [R10 预留核对工具](usage-reconciliation.md)先预览，再按其停机和审计要求执行。
4. 无法确认时继续保留预留。结算存储恢复后，新请求应正常结算；这不会自动补齐旧记录或证明旧费用为零。

此处没有资金退款，也没有自动补偿业务副作用。事件输出仍是尽力写入，进程被杀、磁盘或日志故障可能造成记录缺口。

## 离线故障验证

`tests/test_usage_finalization_failures.py` 使用真实临时 SQLite、合成团队登录与故障注入，覆盖：

- 正常 201/422、业务提交副作用后兜底 500；客户端无法伪造内部状态。
- 结算提交前失败，以及实际提交后回执失败；均只尝试一次。
- 后续审计或发送失败不逆转受理；取消和清理异常不泄漏租户上下文。
- 日志写入失败不改变业务响应；两组织并发与存储恢复不串用量，也不自动改写旧预留。
- `accepted`、`rejected`、`unknown` 的诊断字段及敏感标记负例。

依赖准备好后，在仓库根目录运行：

```bash
.venv/bin/python - <<'PY'
import sys, tempfile
sys.path.insert(0, 'scripts')
from evaluate_industrial_faq import configure_offline
with tempfile.TemporaryDirectory(prefix='usage-finalization-tests-') as folder:
    configure_offline(folder)
    sys.path.insert(0, 'backend')
    import pytest
    raise SystemExit(pytest.main(['tests/test_usage_finalization_failures.py', '-q']))
PY
```

没有引入外部通知、磁盘监测或通用告警面板；本改动只处理已确认的用量结算故障路径。
