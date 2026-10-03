import { useWorkspace } from '../components/WorkspaceContext';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  SourceHubCategory, SourceHubItem, SourceHubMode, SourceHubQuery,
  SourceHubResponse, SourceHubStatus, SourceHubWindow, GitHubProject, GitHubSourceResponse, sourceHub,
} from '../api/sourceHub';
import { EvidenceSeed } from '../api/evidence';

const DEFAULT_QUERY: SourceHubQuery = { window: '24h', mode: 'selected', q: '', category: '' };
const CATEGORIES: Array<{ value: SourceHubCategory; label: string }> = [
  { value: '', label: '全部分类' },
  { value: 'ai-models', label: 'AI 模型' },
  { value: 'ai-products', label: 'AI 产品' },
  { value: 'industry', label: '行业动态' },
  { value: 'paper', label: '论文研究' },
  { value: 'tip', label: '使用技巧' },
];
const OFFICIAL_MCP = 'https://aihot.news/api/mcp';

function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function ExternalLink({ url, children, className = '' }: { url?: string | null; children: React.ReactNode; className?: string }) {
  const href = safeExternalUrl(url);
  return href
    ? <a className={className} href={href} target="_blank" rel="noopener noreferrer">{children}<span aria-hidden="true"> ↗</span><span className="source-hub-sr-only">（新窗口打开）</span></a>
    : <span className={`source-hub-unavailable ${className}`}>{children}（链接不可用）</span>;
}

function formatTime(value: string | null | undefined): string {
  if (!value) return '未知';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '未知' : date.toLocaleString('zh-CN', { hour12: false });
}

function categoryLabel(value: string | null): string {
  return CATEGORIES.find(item => item.value === value)?.label || value || '未分类';
}

function queryKey(query: SourceHubQuery): string {
  return JSON.stringify([query.window, query.mode, query.category, query.q.trim()]);
}

function queryLabel(query: SourceHubQuery): string {
  return [query.window === '24h' ? '近 24 小时' : '近 7 天', query.mode === 'selected' ? '精选' : '全部',
    categoryLabel(query.category), query.q ? `关键词：${query.q}` : '不限关键词'].join(' · ');
}

function verificationNote(item: SourceHubItem): string {
  return [
    `# ${item.title}`,
    '',
    '状态：来源待核验，请阅读原文后自行撰写笔记。此记录不含 AIHOT 摘要。',
    '',
    `原文链接：${safeExternalUrl(item.links.original) || '未提供有效链接'}`,
    `AIHOT 阅读链接：${safeExternalUrl(item.links.aihot) || '未提供有效链接'}`,
    `原文发布时间：${formatTime(item.publishedAt)}`,
    `AIHOT 发现时间：${formatTime(item.discoveredAt)}`,
  ].join('\n');
}

export default function SourceHubPage({onCreateEvidence,onNavigate}:{onCreateEvidence?:(seed:EvidenceSeed)=>void;onNavigate?:(page:string)=>void}) {
  const {canWrite}=useWorkspace();
  const [hubProvider,setHubProvider]=useState<'aihot'|'github'>('aihot');
  const [metadata, setMetadata] = useState<SourceHubStatus | null>(null);
  const [response, setResponse] = useState<SourceHubResponse | null>(null);
  const [form, setForm] = useState<SourceHubQuery>({ ...DEFAULT_QUERY });
  const [activeQuery, setActiveQuery] = useState<SourceHubQuery>({ ...DEFAULT_QUERY });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [formError, setFormError] = useState('');
  const [notice, setNotice] = useState('');
  const [copyError, setCopyError] = useState('');
  const [copyFallback, setCopyFallback] = useState('');
  const [now, setNow] = useState(Date.now());
  const requestNumber = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(false);

  const read = useCallback(async (query: SourceHubQuery, signal?: AbortSignal) => {
    const number = ++requestNumber.current;
    const key = queryKey(query);
    setActiveQuery(query);
    // The server owns cache expiry and revocation. Do not restore client snapshots.
    setResponse(null);
    setSelectedId(null);
    setBusy(true);
    setError('');
    setNotice('');
    setCopyError('');
    setCopyFallback('');
    try {
      const data = await sourceHub.read(query, signal);
      if (!mounted.current || signal?.aborted || number !== requestNumber.current) return;
      if (queryKey(data.query) !== key) throw new Error('返回内容与当前筛选不一致，请重新读取。');
      setResponse(data);
      setSelectedId(data.items[0]?.id || null);
      if (data.refresh.status === 'error') {
        setError(data.refresh.error || '本次读取失败，请稍后重试。');
      }
    } catch (failure) {
      if (!mounted.current || signal?.aborted || number !== requestNumber.current) return;
      setResponse(null);
      setSelectedId(null);
      setError(failure instanceof Error ? failure.message : '本次读取失败，请稍后重试。');
    } finally {
      if (mounted.current && !signal?.aborted && number === requestNumber.current) setBusy(false);
    }
  }, []);

  const loadStatus = useCallback(async () => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setBusy(true);
    setError('');
    setMetadata(null);
    setResponse(null);
    try {
      const data = await sourceHub.status(current.signal);
      if (!mounted.current || current.signal.aborted) return;
      setMetadata(data);
      if (data.enabled) await read(DEFAULT_QUERY, current.signal);
      else { setResponse(null); setBusy(false); }
    } catch (failure) {
      if (!mounted.current || current.signal.aborted) return;
      setError(failure instanceof Error ? failure.message : '无法读取信源配置，请重试。');
      setBusy(false);
    }
  }, [read]);

  useEffect(() => {
    mounted.current = true;
    if(hubProvider==='aihot')void loadStatus();
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => { mounted.current = false; controller.current?.abort(); window.clearInterval(timer); };
  }, [loadStatus,hubProvider]);

  const runQuery = (query: SourceHubQuery) => {
    controller.current?.abort();
    controller.current = new AbortController();
    void read(query, controller.current.signal);
  };
  const selected = response?.items.find(item => item.id === selectedId) || null;
  const source = response?.source || metadata;
  const enabled = Boolean(metadata?.enabled && response?.refresh.status !== 'disabled');
  const nextRefresh = response?.refresh.next_refresh_at ? new Date(response.refresh.next_refresh_at).getTime() : 0;
  const remaining = Number.isFinite(nextRefresh) ? Math.max(0, Math.ceil((nextRefresh - now) / 1000)) : 0;
  const stale = Boolean(response?.refresh.stale);
  const pendingFilter = queryKey(form) !== queryKey(activeQuery);
  const sourceCount = new Set(response?.items.map(item => item.source.name).filter(Boolean)).size;
  const officialMcp = safeExternalUrl(source?.mcp_url) || OFFICIAL_MCP;

  const copy = async (text: string, success: string) => {
    setCopyError(''); setCopyFallback(''); setNotice('');
    try {
      if (!navigator.clipboard?.writeText) throw new Error('当前浏览器没有提供剪贴板权限');
      await navigator.clipboard.writeText(text);
      if (mounted.current) setNotice(success);
    } catch {
      if (mounted.current) { setCopyError('复制失败。请在下方文本框中选中内容后手动复制。'); setCopyFallback(text); }
    }
  };

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const query = { ...form, q: form.q.trim() };
    if (query.q && (Array.from(query.q).length < 2 || Array.from(query.q).length > 200)) {
      setFormError('关键词请输入 2–200 个字，或留空查看列表。'); return;
    }
    setFormError('');
    if (!enabled || busy) return;
    if (queryKey(query) === queryKey(activeQuery) && remaining > 0) {
      setFormError(`当前结果需等待 ${remaining} 秒后才能再次刷新。`); return;
    }
    setForm(query);
    runQuery(query);
  };

  const tabs=<div className="source-hub-provider-tabs" aria-label="选择信源"><button className={`btn ${hubProvider==='aihot'?'active':''}`} aria-pressed={hubProvider==='aihot'} onClick={()=>setHubProvider('aihot')}>AIHOT 动态</button><button className={`btn ${hubProvider==='github'?'active':''}`} aria-pressed={hubProvider==='github'} onClick={()=>setHubProvider('github')}>GitHub 项目发布</button></div>;
  if(hubProvider==='github')return <div className="source-hub-page">{tabs}<GitHubSourceView onCreateEvidence={onCreateEvidence} onNavigate={onNavigate}/></div>;

  return <div className="source-hub-page">{tabs}
    <div className="page-header source-hub-heading">
      <div><h1>信源台</h1><p>从 AIHOT 发现 AI 动态，回到原文核验，再整理成自己的知识。</p></div>
      <ExternalLink url={source?.homepage_url || 'https://aihot.news/agent'} className="btn">打开 AIHOT</ExternalLink>
    </div>

    <section className="source-hub-source" aria-label="信源信息">
      <div><span className="source-hub-kicker">外部信源</span><h2>{source?.name || 'AIHOT'} <span className="pill">AI 聚合与摘要</span></h2>
        <p>保留原文、来源和时间，帮助个人学习与资料核验。网页在新窗口打开。</p>
      </div>
      <div className="source-hub-source-state"><span className={`source-hub-state ${enabled ? 'is-ready' : ''}`}>{busy && !metadata ? '检查读取状态…' : !metadata ? '状态未知' : enabled ? '读取已启用' : '读取未启用'}</span><small>个人学习使用 · 商业对外使用须先取得书面授权</small>{source?.terms_url && <ExternalLink url={source.terms_url}>查看使用条款</ExternalLink>}</div>
    </section>

    {!metadata && !busy && <div className="form-actions source-hub-retry"><button className="btn" onClick={() => void loadStatus()}>重新检查读取状态</button></div>}
    {metadata && !enabled && <div className="notice-strip" role="status">当前工作空间未启用 AIHOT 读取。<span>请在连接中心查看开关与授权状态。你仍可以打开原站阅读。</span>{onNavigate&&<button className="btn btn-sm" onClick={()=>onNavigate('connections')}>前往连接中心</button>}</div>}
    {error && <div className="feedback error" role="alert">{error}{stale && <div>下方为当前筛选的上次读取数据，并非本次更新结果。</div>}</div>}
    {notice && <div className="feedback success" role="status">{notice}</div>}
    {copyError && <div className="feedback error" role="alert">{copyError}<textarea className="source-hub-copy-fallback" aria-label="待手动复制的内容" value={copyFallback} readOnly rows={6} onFocus={event => event.currentTarget.select()} /></div>}

    <form className="source-hub-filters" onSubmit={submit} aria-label="筛选信源">
      <div className="form-group"><label htmlFor="source-window">时间范围</label><select id="source-window" disabled={!enabled || busy} value={form.window} onChange={event => setForm({ ...form, window: event.target.value as SourceHubWindow })}><option value="24h">近 24 小时</option><option value="7d">近 7 天</option></select></div>
      <div className="form-group"><label htmlFor="source-mode">推荐范围</label><select id="source-mode" disabled={!enabled || busy} value={form.mode} onChange={event => setForm({ ...form, mode: event.target.value as SourceHubMode })}><option value="selected">精选</option><option value="all">全部</option></select></div>
      <div className="form-group"><label htmlFor="source-category">内容分类</label><select id="source-category" disabled={!enabled || busy} value={form.category} onChange={event => setForm({ ...form, category: event.target.value as SourceHubCategory })}>{CATEGORIES.map(category => <option key={category.value} value={category.value}>{category.label}</option>)}</select></div>
      <div className="form-group source-hub-search"><label htmlFor="source-query">关键词（可选）</label><input id="source-query" disabled={!enabled || busy} value={form.q} onChange={event => { setForm({ ...form, q: event.target.value }); setFormError(''); }} placeholder="输入 2–200 个字，提交后搜索" aria-describedby={formError ? 'source-form-error' : undefined} /></div>
      <button className="btn btn-primary" type="submit" disabled={!enabled || busy || (!pendingFilter && remaining > 0)}>查询信源</button>
      <p className="source-hub-filter-note">时间范围按 AIHOT 时间轴筛选；原文发布时间在条目中另列。</p>
      {formError && <div id="source-form-error" className="source-hub-form-error" role="alert">{formError}</div>}
    </form>

    <div className="source-hub-results-bar">
      <div><strong>{response ? `${response.items.length} 条动态 · ${sourceCount} 个上游来源` : '等待读取'}</strong><p>{queryLabel(activeQuery)}{pendingFilter && ' · 筛选已修改，提交后生效'}</p></div>
      <button className="btn" disabled={!enabled || busy || remaining > 0} onClick={() => runQuery(activeQuery)}>{busy ? '读取中…' : remaining > 0 ? `${remaining} 秒后可刷新` : '刷新当前结果'}</button>
    </div>
    {response && <div className={`source-hub-freshness ${stale ? 'is-stale' : ''}`} role="status">
      <span>{stale ? '上次数据 · 本次未更新' : response.refresh.status === 'disabled' ? '读取未启用' : response.refresh.from_cache ? '读取缓存' : '本次读取结果'}</span>
      <span>最近尝试：{formatTime(response.refresh.last_attempt_at)}</span><span>最近成功：{formatTime(response.refresh.last_success_at)}</span>
      {response.refresh.next_refresh_at && <span>下次可刷新：{formatTime(response.refresh.next_refresh_at)}</span>}
    </div>}

    <div className="source-hub-layout" aria-busy={busy}>
      <section className="source-hub-list" aria-label="动态列表">
        {!response?.items.length && <div className="source-hub-empty">{busy ? '正在读取所选信源…' : !enabled ? '读取启用后，这里会显示实际获取的动态。' : error ? '本次未能取得内容，请稍后重试或前往原站阅读。' : '当前筛选没有动态。可以扩大时间范围、切换全部或调整关键词。'}</div>}
        {response?.items.map(item => <button type="button" key={item.id} className={`source-hub-item ${selectedId === item.id ? 'is-selected' : ''}`} aria-pressed={selectedId === item.id} onClick={() => { setSelectedId(item.id); setNotice(''); setCopyError(''); setCopyFallback(''); }}>
          <span className="source-hub-item-meta"><span>{categoryLabel(item.category)}</span>{item.selected && <span>AIHOT 精选</span>}</span>
          <strong>{item.title}</strong><p>{item.summary || 'AIHOT 未提供摘要。'}</p>
          <span className="source-hub-item-source">{item.source.name || '来源未标明'}</span><span className="source-hub-item-time">原文发布：{formatTime(item.publishedAt)}</span><span className="source-hub-verification">AI 摘要 · 待核验</span>
        </button>)}
        {response?.page.hasMore && <div className="source-hub-more">此处最多显示 30 条。<ExternalLink url={source?.homepage_url || 'https://aihot.news/agent'}>前往 AIHOT 查看更多</ExternalLink></div>}
      </section>
      <section className="source-hub-detail" aria-label="动态详情">
        {selected ? <article>
          <div className="source-hub-detail-tags"><span className="source-hub-verification">AI 摘要 · 待核验</span><span className="pill">{categoryLabel(selected.category)}</span></div>
          <h2>{selected.title}</h2>
          {selected.originalTitle && selected.originalTitle !== selected.title && <p className="source-hub-original-title">原文标题：{selected.originalTitle}</p>}
          <dl className="source-hub-metadata"><div><dt>上游来源</dt><dd>{selected.source.name || '未标明'}</dd></div><div><dt>原文发布时间</dt><dd>{formatTime(selected.publishedAt)}</dd></div><div><dt>AIHOT 发现时间</dt><dd>{formatTime(selected.discoveredAt)}</dd></div>{selected.score !== null && <div><dt>AIHOT 推荐分</dt><dd>{selected.score} <small>用于推荐排序，不代表可信度</small></dd></div>}</dl>
          <div className="notice-strip">先回到原文核对关键事实，再用于自己的资料或内容。<span>AIHOT 摘要与推荐理由尚未经本项目核验；本站不会自动将其加入知识库。</span></div>
          <h3>AIHOT 摘要</h3><p className="source-hub-summary">{selected.summary || 'AIHOT 未提供摘要，请打开原文查看。'}</p>
          {selected.reason && <div className="source-hub-reason"><h3>AIHOT 推荐理由</h3><p>{selected.reason}</p></div>}
          <div className="form-actions source-hub-detail-actions"><ExternalLink url={selected.links.original} className="btn btn-primary">打开原文核验</ExternalLink><ExternalLink url={selected.links.aihot} className="btn">在 AIHOT 阅读</ExternalLink><button className="btn" disabled={!canWrite||!onCreateEvidence||!safeExternalUrl(selected.links.original||selected.links.aihot)} onClick={()=>onCreateEvidence?.({title:selected.originalTitle||selected.title,source_url:safeExternalUrl(selected.links.original||selected.links.aihot)!,provider:'aihot',version_label:selected.version_label||''})}>创建本人核验笔记</button><button className="btn" onClick={() => void copy(verificationNote(selected), '标题、链接与时间已复制，不含 AIHOT 摘要。')}>复制来源信息</button></div>
          <p className="subtle">创建笔记只带入标题和链接，正文及逐条引用由你核对后填写。</p>
          <p className="source-hub-attribution">聚合与摘要来自 {selected.attribution ? <ExternalLink url={selected.attribution.url}>{selected.attribution.name || 'AIHOT'}</ExternalLink> : 'AIHOT'}。上游条目可能为媒体或社区转述，核验时请继续追溯一手资料。</p>
        </article> : <div className="source-hub-empty">选择一条动态，查看摘要、来源和核验链接。</div>}
      </section>
    </div>

    <details className="source-hub-explainer"><summary>怎样看待不同信源？</summary><p>官方资料可用于核对产品说明与公告；媒体提供报道与采访；社区提供个人经验与讨论；聚合平台帮助发现信息。它们承担不同角色，不自动代表可信或不可信。请结合原文、时间、证据和其他独立来源判断，尤其留意摘要省略的条件。</p></details>
    <section className="source-hub-mcp" aria-label="官方 MCP 接入信息"><div><h2>AIHOT 官方 MCP</h2><p>供支持 MCP 的 Agent 客户端配置使用。这里展示官方接入地址，连接状态需在对应客户端验证。</p><label htmlFor="source-mcp-url">官方接入地址</label><input id="source-mcp-url" readOnly value={officialMcp} onFocus={event => event.currentTarget.select()} /></div><button className="btn" onClick={() => void copy(officialMcp, 'AIHOT 官方 MCP 地址已复制。')}>复制接入地址</button></section>
  </div>;
}

function GitHubSourceView({onCreateEvidence,onNavigate}:{onCreateEvidence?:(seed:EvidenceSeed)=>void;onNavigate?:(page:string)=>void}) {
  const {canWrite}=useWorkspace();
  const [metadata,setMetadata]=useState<SourceHubStatus|null>(null);
  const [response,setResponse]=useState<GitHubSourceResponse|null>(null);
  const [project,setProject]=useState<GitHubProject>('langchain');
  const [activeProject,setActiveProject]=useState<GitHubProject>('langchain');
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const [busy,setBusy]=useState(true);
  const [error,setError]=useState('');
  const [now,setNow]=useState(Date.now());
  const controller=useRef<AbortController|null>(null);
  const mounted=useRef(false);
  const sequence=useRef(0);
  const read=useCallback(async(value:GitHubProject,signal?:AbortSignal)=>{
    const current=++sequence.current;setResponse(null);setSelectedId(null);setActiveProject(value);setBusy(true);setError('');
    try{const result=await sourceHub.githubRead(value,signal);if(!mounted.current||signal?.aborted||current!==sequence.current)return;
      if(result.query.project!==value)throw new Error('返回项目与当前选择不一致，请重新读取。');
      setResponse(result);setSelectedId(result.items[0]?.id||null);if(result.refresh.status==='error')setError(result.refresh.error||'项目发布读取失败。');
    }catch(e){if(mounted.current&&!signal?.aborted&&current===sequence.current)setError(e instanceof Error?e.message:String(e));}
    finally{if(mounted.current&&!signal?.aborted&&current===sequence.current)setBusy(false);}
  },[]);
  const load=useCallback(async()=>{controller.current?.abort();const current=new AbortController();controller.current=current;setBusy(true);setError('');setMetadata(null);setResponse(null);
    try{const result=await sourceHub.githubStatus(current.signal);if(!mounted.current||current.signal.aborted)return;setMetadata(result);if(result.enabled)await read('langchain',current.signal);else setBusy(false);}
    catch(e){if(mounted.current&&!current.signal.aborted){setError(e instanceof Error?e.message:String(e));setBusy(false);}}
  },[read]);
  useEffect(()=>{mounted.current=true;void load();const timer=window.setInterval(()=>setNow(Date.now()),1000);return()=>{mounted.current=false;controller.current?.abort();window.clearInterval(timer);};},[load]);
  const enabled=Boolean(metadata?.enabled&&response?.refresh.status!=='disabled');
  const next=response?.refresh.next_refresh_at?Date.parse(response.refresh.next_refresh_at):0;
  const remaining=Number.isFinite(next)?Math.max(0,Math.ceil((next-now)/1000)):0;
  const run=(value:GitHubProject)=>{if(busy||!enabled||(value===activeProject&&remaining>0))return;controller.current?.abort();controller.current=new AbortController();void read(value,controller.current.signal);};
  const selected=response?.items.find(item=>item.id===selectedId);
  return <>
    <div className="page-header source-hub-heading"><div><h1>信源台 · GitHub</h1><p>查看项目维护者发布的版本说明，核对适用版本后形成自己的笔记。</p></div><ExternalLink url={metadata?.homepage_url||'https://github.com'} className="btn">打开 GitHub</ExternalLink></div>
    <div className="notice-strip">项目维护者发布说明 · 不等于独立测试结论<span>仅查看预设项目的公开发布记录；代码许可、功能效果及升级影响仍需逐项核对。</span></div>
    {error&&<div className="feedback error" role="alert">{error}{response?.refresh.stale&&<div>显示的是当前项目的上次数据，并非本次更新结果。</div>}</div>}
    {!metadata&&!busy&&<button className="btn" onClick={()=>void load()}>重新检查读取状态</button>}
    {metadata&&!enabled&&<div className="notice-strip">当前工作空间未启用 GitHub 读取。可以打开项目原站阅读。{onNavigate&&<button className="btn btn-sm" onClick={()=>onNavigate('connections')}>前往连接中心</button>}</div>}
    <form className="form-actions" onSubmit={e=>{e.preventDefault();run(project);}}><label htmlFor="github-project">项目</label><select id="github-project" value={project} disabled={busy||!enabled} onChange={e=>setProject(e.target.value as GitHubProject)}><option value="langchain">LangChain</option><option value="dify">Dify</option><option value="ragflow">RAGFlow</option></select><button className="btn btn-primary" disabled={busy||!enabled||(project===activeProject&&remaining>0)}>读取项目发布</button><button className="btn" type="button" disabled={busy||!enabled||remaining>0} onClick={()=>run(activeProject)}>{busy?'读取中…':remaining>0?`${remaining} 秒后可刷新`:'刷新当前项目'}</button></form>
    <div className="source-hub-results-bar"><strong>{activeProject} · {response?.items.length||0} 条发布记录</strong><small>最多显示 20 条{project!==activeProject?' · 新选择提交后生效':''}</small></div>
    {response&&<div className={`source-hub-freshness ${response.refresh.stale?'is-stale':''}`} role="status"><span>{response.refresh.stale?'上次数据 · 本次未更新':response.refresh.from_cache?'读取缓存':'本次读取结果'}</span><span>最近成功：{formatTime(response.refresh.last_success_at)}</span><span>下次可刷新：{formatTime(response.refresh.next_refresh_at)}</span></div>}
    <div className="source-hub-layout"><section className="source-hub-list" aria-label="GitHub 发布列表">{!response?.items.length&&<div className="source-hub-empty">{busy?'正在读取发布记录…':error?'本次没有取得可用记录。':enabled?'该项目暂无可显示的发布记录。':'读取启用后显示实际发布记录。'}</div>}{response?.items.map(item=><button key={item.id} className={`source-hub-item ${selectedId===item.id?'is-selected':''}`} aria-pressed={selectedId===item.id} onClick={()=>setSelectedId(item.id)}><span className="source-hub-item-meta">维护者发布 · {item.version_label||'版本未标明'}</span><strong>{item.title}</strong><span className="source-hub-item-time">发布时间：{formatTime(item.publishedAt)}</span><span className="source-hub-verification">待本人核验</span></button>)}{response?.page.hasMore&&<div className="source-hub-more">更多版本请前往项目发布页面查看。</div>}</section><section className="source-hub-detail" aria-label="GitHub 发布详情">{selected?<article><span className="source-hub-verification">维护者发布说明 · 待本人核验</span><h2>{selected.title}</h2><dl className="source-hub-metadata"><div><dt>项目来源</dt><dd>{selected.source.name}</dd></div><div><dt>版本</dt><dd>{selected.version_label||'未登记'}</dd></div><div><dt>发布时间</dt><dd>{formatTime(selected.publishedAt)}</dd></div><div><dt>本地发现时间</dt><dd>{formatTime(selected.discoveredAt)}</dd></div></dl><p className="source-hub-summary">{selected.summary||'该发布记录没有正文，请打开 GitHub 查看。'}</p><div className="form-actions source-hub-detail-actions"><ExternalLink url={selected.links.original} className="btn btn-primary">查看项目发布原文</ExternalLink><button className="btn" disabled={!canWrite||!onCreateEvidence||!safeExternalUrl(selected.links.original)} onClick={()=>onCreateEvidence?.({title:selected.title,source_url:safeExternalUrl(selected.links.original)!,provider:'github',version_label:selected.version_label||''})}>创建本人核验笔记</button></div><p className="subtle">只带入标题、版本和原文链接。阅读并核对版本说明后，自行填写笔记与引用。</p></article>:<div className="source-hub-empty">选择一个版本查看发布原文与来源。</div>}</section></div>
  </>;
}
