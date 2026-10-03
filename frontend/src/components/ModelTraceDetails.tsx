import { useEffect, useState } from 'react';
import { getModelTrace, ModelTrace } from '../api/modelTrace';
import '../styles/model-trace.css';

const callKinds: Record<string, string> = {
  initial: '首次请求', format_fallback: '格式回退', json_retry: '重新生成 JSON',
  json_repair: '修复 JSON', local_rule: '本地规则',
};
const costLabels: Record<string, string> = {
  missing_usage: '用量缺失', unpriced: '未配置价格', failed: '失败请求费用未知',
  local_rule: '不适用', invalid_pricing: '价格配置无效', legacy_unknown: '历史费用未知',
};
const known = (value: number | null) => value == null ? '未知' : value.toLocaleString();

export default function ModelTraceDetails({ runId, updatedAt, revision, steps }: {
  runId: number; updatedAt: string; revision: string; steps: Array<{key: string; label: string}>;
}) {
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [data, setData] = useState<ModelTrace | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  useEffect(() => { setOffset(0); setData(null); }, [runId]);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoading(true); setError(''); setData(null);
    getModelTrace(runId, offset).then(value => { if (active) setData(value); })
      .catch(() => { if (active) setError('暂时无法读取调用记录，请重新读取。'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [runId, updatedAt, revision, offset, open, refresh]);
  const summary = data?.summary;
  return <details className="calm-details model-trace" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>模型调用与费用</summary>
    {loading && <p className="subtle" role="status">正在读取调用记录…</p>}
    {error && <p className="feedback error" role="alert">{error} <button className="text-action" onClick={() => setRefresh(value => value + 1)}>重新读取</button></p>}
    {data && summary && <>
      <p className="subtle">已记录外部请求 {summary.recorded_external_request_count} 次 · 本地规则 {summary.local_rule_call_count} 次 · 失败 {summary.failed_call_count} 次。历史未关联或未成功写入的日志不计入；没有记录不等于没有费用。</p>
      {summary.recorded_external_request_count > 0 ? <div className="model-trace-totals">
        <span>外部请求 token：<strong>{known(summary.total_tokens)}</strong></span>
        <span>估算费用：<strong>{summary.estimated_cost == null ? '未知' : `${summary.cost_currency} ${summary.estimated_cost}`}</strong></span>
        <span>供应商账单：<strong>未接入</strong></span>
      </div> : <p className="subtle">{data.generation_mode === 'local_rules' ? '本任务使用本地规则与摘录。' : ''}尚无关联的外部模型请求记录，模型 token 和费用不作零值推断。</p>}
      <p className="subtle">估算仅在所有已记录外部请求的价格、用量和币种齐备时汇总，不包含人工、设备与支持成本。任务重试会保留前次记录。</p>
      {data.runs.length > 0 && <div className="model-trace-table-wrap"><table className="model-trace-table">
        <caption>调用明细 · 第 {data.offset + 1}–{data.offset + data.runs.length} 条，共 {data.total} 条</caption>
        <thead><tr><th scope="col">步骤 / 尝试</th><th scope="col">模型 / 结果</th><th scope="col">用量与估算</th><th scope="col">追溯</th></tr></thead>
        <tbody>{data.runs.map(row => <tr key={row.id}>
          <td>{steps.find(step => step.key === row.step_key)?.label || row.step_key || '未关联步骤'}<small>任务第 {row.workflow_attempt ?? '未知'} 次尝试 · 请求 {row.request_index ?? '未知'}</small><small>{callKinds[row.call_kind || ''] || row.call_kind || '历史类型未知'}</small></td>
          <td>{row.provider === 'local' ? '本地规则' : row.provider} · {row.model_name}<small>{row.success ? '成功' : `失败 · ${row.error_type || '未知类型'}`}{row.latency_ms != null && ` · ${row.latency_ms} ms`}</small></td>
          <td><small>输入 / 输出：{known(row.prompt_tokens)} / {known(row.completion_tokens)}</small><small>总 token：{known(row.total_tokens)}</small>{row.estimated_cost == null ? costLabels[row.cost_status] || '费用未知' : `${row.cost_currency} ${row.estimated_cost}`}<small>{row.pricing_version ? `价格版本：${row.pricing_version}` : '未记录价格版本'}</small></td>
          <td><details><summary>版本与标识</summary><dl><dt>调用组</dt><dd>{row.invocation_id || '未知'}</dd><dt>指令版本</dt><dd>{row.prompt_version || '未知'}</dd><dt>完整提示词 hash</dt><dd>{row.prompt_hash || '未知'}</dd></dl></details></td>
        </tr>)}</tbody>
      </table></div>}
      {data.total > data.limit && <div className="form-actions"><button className="btn" disabled={data.offset === 0} onClick={() => setOffset(Math.max(0, offset - data.limit))}>上一页</button><button className="btn" disabled={!data.has_more} onClick={() => setOffset(offset + data.limit)}>下一页</button></div>}
    </>}
  </details>;
}
