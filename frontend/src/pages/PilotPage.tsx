import { useEffect, useRef, useState } from 'react';
import { pilotApi, PilotItem, PilotOutcome, PilotReport } from '../api/pilot';
import { useWorkspace } from '../components/WorkspaceContext';
import { formToInput, PilotForm, recordToForm } from '../utils/pilotForm';
import '../styles/pilot.css';

const outcomes: Record<PilotOutcome, string> = {pending: '待客户确认', accepted: '客户验收通过', rejected: '客户退回', abandoned: '已放弃'};
const numberLabel = (value: number | null) => value === null ? '未采集' : `${Number(value.toFixed(1))}`;
function initialDates() {
  const now = new Date();
  const start = new Date(now);
  start.setUTCDate(start.getUTCDate() - 13);
  return {start: start.toISOString().slice(0, 10), end: now.toISOString().slice(0, 10)};
}
function outcomeLabel(item: PilotItem) {
  if (!item.record) return '未登记';
  if (item.record.outcome === 'accepted' && !item.acceptance_current) return '旧版本验收，需复核';
  return outcomes[item.record.outcome];
}

export default function PilotPage({onNavigate}: {onNavigate: (page: string) => void}) {
  const {canWrite} = useWorkspace();
  const [dates, setDates] = useState(initialDates);
  const [range, setRange] = useState(initialDates);
  const [refresh, setRefresh] = useState(0);
  const [report, setReport] = useState<PilotReport | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [form, setForm] = useState<PilotForm>(() => recordToForm(null));
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(''); setReport(null); setSelectedId(null); setForm(recordToForm(null));
    pilotApi.report(range.start, range.end, controller.signal).then(value => {
      if (!controller.signal.aborted) setReport(value);
    }).catch(cause => {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '读取试点记录失败');
    }).finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [range, refresh]);

  const selected = report?.items.find(item => item.run_id === selectedId) ?? null;
  const select = (item: PilotItem) => {
    setSelectedId(item.run_id); setForm(recordToForm(item.record)); setError(''); setNotice('');
  };
  const update = <K extends keyof PilotForm>(key: K, value: PilotForm[K]) => setForm(old => ({...old, [key]: value}));
  const query = () => {
    if (!dates.start || !dates.end || dates.start > dates.end) {setError('请选择有效日期，结束日期不能早于开始日期。'); return;}
    setRange({...dates}); setNotice('');
  };
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!selected || saving || !canWrite) return;
    setError(''); setNotice('');
    let body;
    try {body = formToInput(form);} catch (cause) {setError((cause as Error).message); return;}
    setSaving(true);
    try {
      const saved = await pilotApi.save(selected.run_id, body);
      if (!alive.current) return;
      setForm(recordToForm(saved));
      setNotice('已保存人工记录。客户确认依据与工时仍需按实际情况核实。');
      try {
        const next = await pilotApi.report(range.start, range.end);
        if (alive.current) setReport(next);
      } catch {
        if (alive.current) {setReport(null); setSelectedId(null); setNotice('记录已保存，但汇总刷新失败，请重新查询。');}
      }
    } catch (cause) {
      if (alive.current) setError(`${cause instanceof Error ? cause.message : '保存失败'}。如提示版本冲突，请重新读取记录后核对再保存。`);
    } finally {if (alive.current) setSaving(false);}
  };
  const reloadSelected = async () => {
    if (!selected || saving) return;
    setSaving(true); setError(''); setNotice('');
    try {
      const next = await pilotApi.report(range.start, range.end);
      if (alive.current) {
        const item = next.items.find(value => value.run_id === selected.run_id);
        setReport(next); setSelectedId(item?.run_id ?? null); setForm(recordToForm(item?.record ?? null));
        setNotice(item ? '已重新读取记录及汇总，请核对后保存。' : '任务已不在当前查询范围，请重新选择。');
      }
    } catch (cause) {
      if (alive.current) {setReport(null); setSelectedId(null); setError((cause as Error).message);}
    }
    finally {if (alive.current) setSaving(false);}
  };
  const download = () => {
    if (!report) return;
    const data = {measurement_note: '人工登记，不是独立客户证明。日期按任务创建日 UTC；缺失不补零；仅当前有效验收且三项工时齐全的任务参与工时比较。', ...report};
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json;charset=utf-8'}));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `试点复盘-${report.start_date}-${report.end_date}.json`; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return <div className="pilot-page">
    <div className="page-header"><h1>试点验收</h1><p>把客户确认与人工投入记下来，判断内容交付是否值得重复做。</p></div>
    <div className="notice-strip"><strong>内部审核通过后，再记录客户是否接受交付。</strong><span>这里由团队人工登记，不会自动验证客户确认，也不代表已发布或已付款。工时留空表示未采集；确实没有投入时填写 0。</span></div>
    {error && <div className="feedback error" role="alert">{error}</div>}
    {notice && <div className="notice-strip" role="status">{notice}</div>}
    <section className="panel pilot-query" aria-label="试点查询">
      <div className="form-group"><label htmlFor="pilot-start">任务创建日期起（UTC）</label><input id="pilot-start" type="date" value={dates.start} disabled={saving} onChange={e => setDates({...dates, start: e.target.value})}/></div>
      <div className="form-group"><label htmlFor="pilot-end">任务创建日期止（UTC）</label><input id="pilot-end" type="date" value={dates.end} disabled={saving} onChange={e => setDates({...dates, end: e.target.value})}/></div>
      <button className="btn btn-primary" onClick={query} disabled={loading || saving}>{loading ? '读取中…' : '查询任务'}</button>
      <button className="btn" onClick={() => setRefresh(value => value + 1)} disabled={loading || saving}>刷新</button>
      <button className="btn" onClick={download} disabled={!report || loading || saving}>下载当前复盘 JSON</button>
    </section>
    {report && <>
      <section className="pilot-summary" aria-label="本期汇总">
        <div className="panel"><span>本期全部任务</span><strong>{report.total_runs}</strong><small>未登记 {report.missing_records} · 待确认 {report.pending_runs}</small></div>
        <div className="panel"><span>当前版本验收通过</span><strong>{report.accepted_runs}</strong><small>旧版本需复核 {report.stale_acceptances}</small></div>
        <div className="panel"><span>客户退回 / 放弃</span><strong>{report.rejected_runs} / {report.abandoned_runs}</strong><small>保留失败和退出记录</small></div>
        <div className="panel"><span>可比任务工时变化</span><strong>{report.savings_rate === null ? '暂无可比数据' : `${(report.savings_rate * 100).toFixed(1)}%`}</strong><small>{report.comparable_runs} 个样本 · 节省 {numberLabel(report.saved_minutes_total)} 分钟</small></div>
      </section>
      <p className="subtle">工时变化 =（基线总工时 − 实际操作及支持总工时）÷ 基线总工时。仅统计当前验收有效、基线及两项实际工时齐全的任务；负数表示耗时增加，不是收益率或因果证明。</p>
      <div className="pilot-layout">
        <section className="panel pilot-tasks"><h2>选择交付任务</h2>
          {report.items.length === 0 ? <div className="empty">本日期范围没有内容任务。可以调整日期，或先<button className="text-action" onClick={() => onNavigate('agent')}>创建内容任务</button>。</div> : <div className="stack-list">{report.items.map(item => <button key={item.run_id} className={`list-button ${selectedId === item.run_id ? 'active' : ''}`} disabled={saving} onClick={() => select(item)}>
            <strong>#{item.run_id} · {item.goal}</strong><span>{outcomeLabel(item)}</span><small>{item.record?.cohort ?? '尚未加入试点批次'} · {item.created_at.slice(0, 10)}</small>
          </button>)}</div>}
        </section>
        <section className="panel pilot-editor"><h2>{selected ? `任务 #${selected.run_id} 的验收记录` : '记录客户反馈和工时'}</h2>
          {!selected ? <div className="empty">先选择一个任务。未完成或已放弃的任务也应保留记录。</div> : <form onSubmit={save}>
            <div className="pilot-task-context"><p>{selected.goal}</p><button className="text-action" type="button" disabled={saving} onClick={() => onNavigate(`agent?run=${selected.run_id}`)}>查看内容和内部审核 →</button></div>
            {selected.record?.outcome === 'accepted' && !selected.acceptance_current && <div className="feedback error">原验收对应的内容或引用已失效。旧记录保留供核对，当前版本须重新审核并向客户确认。</div>}
            <fieldset disabled={!canWrite || saving}>
              <div className="form-row">
                <div className="form-group"><label htmlFor="pilot-cohort">试点批次</label><input id="pilot-cohort" maxLength={80} required value={form.cohort} onChange={e => update('cohort', e.target.value)}/></div>
                <div className="form-group"><label htmlFor="pilot-outcome">客户反馈</label><select id="pilot-outcome" value={form.outcome} onChange={e => update('outcome', e.target.value as PilotOutcome)}>{Object.entries(outcomes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></div>
              </div>
              <div className="form-group"><label htmlFor="pilot-baseline">原流程基线（分钟）</label><input id="pilot-baseline" type="number" min="0.01" max="100000" step="any" placeholder="未采集请留空" value={form.baseline_minutes} onChange={e => update('baseline_minutes', e.target.value)}/></div>
              <div className="form-group"><label htmlFor="pilot-baseline-ref">基线对照依据</label><textarea id="pilot-baseline-ref" maxLength={500} rows={2} placeholder="同类历史任务编号、相同内容范围和质量标准；区分实测与估计" value={form.baseline_reference} onChange={e => update('baseline_reference', e.target.value)}/></div>
              <div className="form-row">
                <div className="form-group"><label htmlFor="pilot-actual">实际操作工时（分钟）</label><input id="pilot-actual" type="number" min="0" max="100000" step="any" placeholder="未采集请留空" value={form.actual_work_minutes} onChange={e => update('actual_work_minutes', e.target.value)}/><small>资料整理、写作、审核和全部返工；不含机器等待及下项支持工时。</small></div>
                <div className="form-group"><label htmlFor="pilot-support">实施支持工时（分钟）</label><input id="pilot-support" type="number" min="0" max="100000" step="any" placeholder="未采集请留空" value={form.support_minutes} onChange={e => update('support_minutes', e.target.value)}/><small>部署指导、故障协助等额外人工，不与操作工时重复计算。</small></div>
              </div>
              <div className="form-group"><label htmlFor="pilot-acceptance-ref">客户确认依据</label><textarea id="pilot-acceptance-ref" maxLength={500} rows={2} placeholder="例如确认日期、工单编号和确认内容；请勿填写无关个人信息" value={form.acceptance_reference} onChange={e => update('acceptance_reference', e.target.value)}/></div>
              <div className="form-group"><label htmlFor="pilot-note">退回、放弃原因或测量说明</label><textarea id="pilot-note" maxLength={1000} rows={3} placeholder="包括失败尝试、未计入的初始化成本和仍需核实的项目" value={form.note} onChange={e => update('note', e.target.value)}/></div>
              <p className="subtle">登记“客户验收通过”要求当前内容已通过内部审核，确认依据不能为空。保存会绑定本次内容版本。</p>
              <div className="pilot-actions"><button className="btn btn-primary" type="submit">{saving ? '保存中…' : '保存人工记录'}</button><button className="btn" type="button" onClick={reloadSelected}>重新读取记录</button></div>
            </fieldset>
            {form.version > 0 && <p className="subtle">记录版本 {form.version} · 人工登记 · 尚未验证客户付款或完整交付成本</p>}
          </form>}
        </section>
      </div>
    </>}
  </div>;
}
