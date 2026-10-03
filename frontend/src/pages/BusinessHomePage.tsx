import { useCallback, useEffect, useRef, useState } from 'react';
import { api, AgentRun } from '../api/client';
import { workbench, KnowledgeDocument } from '../api/workbench';
import { useWorkspace } from '../components/WorkspaceContext';

const statuses: Record<string, string> = {pending: '等待开始', running: '正在生成', awaiting_review: '待审核', approved: '已批准可交付', rejected: '需要修改', failed: '需要处理', cancelled: '已取消'};
const queueFilters = [
  {key: 'awaiting_review', label: '待审核', note: '核对内容和来源，再批准交付', empty: '暂时没有等待审核的内容。'},
  {key: 'rejected', label: '需修改', note: '按照审核意见修改后重新送审', empty: '暂时没有退回修改的内容。'},
  {key: 'approved', label: '可交付', note: '已人工批准，可以导出使用', empty: '批准后的内容会出现在这里。'},
] as const;

export default function BusinessHomePage({onNavigate}: {onNavigate: (page: string) => void}) {
  const {organization, canWrite, canReview} = useWorkspace();
  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [tasksReady, setTasksReady] = useState(false);
  const [filter, setFilter] = useState<string>('all');
  const [expanded, setExpanded] = useState(false);
  const requestVersion = useRef(0);
  const load = useCallback(async () => {
    const version = ++requestVersion.current;
    setLoading(true); setError('');
    const result = await Promise.allSettled([workbench.documents(), api.listAgentRuns(50)]);
    if (version !== requestVersion.current) return;
    const errors: string[] = [];
    if (result[0].status === 'fulfilled') { setDocs(result[0].value.items); setTotal(result[0].value.total); }
    else { setDocs([]); setTotal(null); errors.push('资料'); }
    if (result[1].status === 'fulfilled') { setRuns(result[1].value); setTasksReady(true); }
    else { setRuns([]); setTasksReady(false); errors.push('任务'); }
    if (errors.length) setError(`${errors.join('和')}暂时未能读取，请重试。`);
    setLoading(false);
  }, []);
  useEffect(() => { void load(); return () => { requestVersion.current += 1; }; }, [load]);
  useEffect(() => { setExpanded(false); }, [filter]);
  const selectedQueue = queueFilters.find(item => item.key === filter);
  const visibleRuns = filter === 'all' ? runs : runs.filter(item => item.status === filter);
  const failedCount = runs.filter(item => item.status === 'failed').length;
  const openRun = (run: AgentRun) => {
    if (run.status === 'approved' && run.draft_id) {
      onNavigate(`drafts?draft=${run.draft_id}&run=${run.id}${run.selected_topic_id ? `&topic=${run.selected_topic_id}` : ''}`);
    } else onNavigate(`agent?run=${run.id}`);
  };

  return <div className="commercial-home">
    <div className="page-header home-heading"><div><span className="eyebrow">内容工作台</span><h1>{organization?.name || '把专业积累，变成可交付的内容。'}</h1><p>从品牌资料到内容审核，把每一步接起来。</p></div><button className="btn" disabled={loading} onClick={() => void load()}>{loading ? '更新中…' : '刷新'}</button></div>
    {error && <div className="feedback error" role="alert">{error}</div>}
    <section className="commercial-start">
      <div><span className="eyebrow">开始一份内容</span><h2>明确要求，<br/>让创作有章可循。</h2><p>选好品牌和资料，创建内容任务。生成草稿后检查、修改、批准，再导出给需要的人。</p><div className="form-actions"><button className="btn btn-primary" disabled={!canWrite} onClick={() => onNavigate('agent')}>创建内容任务 <span aria-hidden="true">→</span></button><button className="btn" onClick={() => onNavigate('brands')}>准备品牌资料</button></div>{!canWrite && <p className="commercial-role-hint">{canReview ? '当前角色可以审核内容，创建任务由内容编辑或所有者操作。' : '当前为查看权限，可以打开已有内容和资料。'}</p>}</div>
      <ol className="commercial-flow" aria-label="内容交付流程"><li><b>01</b><span>准备资料<small>品牌表达、真实产品与案例</small></span></li><li><b>02</b><span>创建任务<small>对象、目的和具体要求</small></span></li><li><b>03</b><span>检查与审核<small>核对事实，修改后确认</small></span></li><li><b>04</b><span>导出与复盘<small>手动发布，记录实际效果</small></span></li></ol>
    </section>
    <section className="home-queue-overview" aria-label="内容处理状态">{queueFilters.map(item => {
      const count = runs.filter(run => run.status === item.key).length;
      return <button key={item.key} className={`home-queue-card ${filter === item.key ? 'selected' : ''}`} aria-pressed={filter === item.key} disabled={!tasksReady} onClick={() => setFilter(filter === item.key ? 'all' : item.key)}><div><span>{item.label}</span><strong>{tasksReady ? count : '—'}</strong></div><small>{item.note}</small></button>;
    })}</section>
    <p className="home-data-scope">状态统计与下方列表来自最近 50 个任务。批准表示可交付，不代表已经发布。{failedCount > 0 && <button className="text-action" onClick={() => setFilter('failed')}>{failedCount} 个任务需要处理 →</button>}</p>
    <div className="commercial-home-columns">
      <section className="panel home-task-list"><div className="section-heading"><div><h2>{selectedQueue ? `${selectedQueue.label}内容` : filter === 'failed' ? '需要处理的任务' : '最近内容任务'}</h2><p className="subtle">{selectedQueue?.note || '打开具体任务，继续上次的工作。'}</p></div>{filter !== 'all' ? <button className="text-action" onClick={() => setFilter('all')}>查看全部</button> : <button className="text-action" onClick={() => onNavigate('agent')}>内容任务 →</button>}</div>
        {loading && !runs.length ? <p className="empty">正在读取任务…</p> : !tasksReady ? <div className="calm-empty"><h3>任务暂时无法读取</h3><p>请刷新后再查看，不会影响已保存的内容。</p></div> : !visibleRuns.length ? <div className="calm-empty"><span aria-hidden="true">▤</span><h3>{filter === 'all' ? '还没有内容任务' : '当前没有待处理内容'}</h3><p>{selectedQueue?.empty || (filter === 'all' ? '先选好品牌和资料，再写下这次内容的目标。' : '可以切换到全部，查看其他内容。')}</p>{filter === 'all' && <button className="btn" disabled={!canWrite} onClick={() => onNavigate('agent')}>创建第一份内容</button>}</div> : visibleRuns.slice(0, expanded ? 50 : 6).map(run => <button className="recent-work" key={run.id} onClick={() => openRun(run)}><span className={`status-dot status-${run.status}`}/><div><strong>{run.goal}</strong><small>任务 #{run.id} · {statuses[run.status] || run.status}</small></div><span className={`task-state-label state-${run.status}`}>{run.status === 'approved' ? '打开交付' : run.status === 'rejected' && canWrite ? '去修改' : run.status === 'awaiting_review' && canReview ? '去审核' : '查看'}</span></button>)}
        {visibleRuns.length > 6 && <button className="text-action home-expand-tasks" onClick={() => setExpanded(!expanded)}>{expanded ? '收起任务列表' : `展开其余 ${visibleRuns.length - 6} 个任务 ↓`}</button>}
      </section>
      <aside className="panel home-materials"><div className="section-heading"><h2>创作资料</h2><button className="text-action" onClick={() => onNavigate('knowledge')}>资料库 →</button></div><p className="subtle">默认知识库已保存 {total ?? '—'} 份资料。使用前请确认仍适用于本次内容。</p>
        {loading && total === null ? <p className="empty">正在读取资料…</p> : total === null ? <p className="empty">资料暂时无法读取，请刷新重试。</p> : !docs.length ? <div className="calm-empty"><span aria-hidden="true">▧</span><h3>从你已有的资料开始</h3><p>导入产品说明、服务问答和经过确认的真实案例。</p><button className="btn" disabled={!canWrite} onClick={() => onNavigate('knowledge')}>添加资料</button></div> : docs.slice(0, 3).map(doc => <button className="recent-work" key={doc.id} onClick={() => onNavigate(`knowledge?kb=${doc.knowledge_base_id}`)}><span className="document-symbol" aria-hidden="true">▧</span><div><strong>{doc.title}</strong><small>查看资料与原始出处</small></div><span aria-hidden="true">→</span></button>)}
        <div className="home-material-action"><strong>先定表达，再开始创作</strong><p>维护品牌背景、目标受众和表达要求，减少每次重复说明。</p><button className="text-action" onClick={() => onNavigate('brands')}>管理品牌档案 →</button></div>
      </aside>
    </div>
  </div>;
}
