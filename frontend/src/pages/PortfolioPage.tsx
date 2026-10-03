import { useWorkspace } from '../components/WorkspaceContext';
import { useEffect, useState } from 'react';
import { Portfolio, RetrievalStatus, workbench } from '../api/workbench';

export default function PortfolioPage({onNavigate}: {onNavigate: (page:string)=>void}) {
  const {canWrite}=useWorkspace();
  const [data,setData]=useState<Portfolio|null>(null);
  const [status,setStatus]=useState<RetrievalStatus|null>(null);
  const [error,setError]=useState('');const [busy,setBusy]=useState(false);const [message,setMessage]=useState('');
  const refresh=async()=>{const [p,s]=await Promise.all([workbench.portfolio(),workbench.status()]);setData(p);setStatus(s);};
  useEffect(()=>{refresh().catch(e=>setError(String(e)));},[]);
  const demo=async()=>{setBusy(true);setError('');try{const r=await workbench.loadDemo();await refresh();setMessage(`已准备 ${r.documents} 份演示资料，可以开始检索和创作。`);}catch(e){setError(String(e));}finally{setBusy(false);}};
  const skills=[
    ['01','资料与检索','理解资料入库、切块、语义向量、检索与引用。','knowledge','backend/app/agent_core/rag_service.py'],
    ['02','任务与审核','观察工作流、分支、失败重试与人工审核。','agent','backend/app/services/content_growth_agent.py'],
    ['03','效果与边界','用固定问题检查召回与拒答，追踪失败样本。','rag','scripts/evaluate_rag.py'],
    ['04','接口与交付','从页面追到 API、数据表和测试，独立启动项目。','architecture','docs/learning-guide.md'],
  ];
  return <div className="portfolio-page">
    <section className="workspace-hero"><div><span className="eyebrow">知源 / CONTENT AGENT</span><h1>让每一份内容，<br/>都有据可查。</h1><p>从资料到草稿，从检索到审核。一个可演示、可追踪、可逐步拆解学习的 AI 应用。</p><div className="form-actions"><button className="btn btn-primary" onClick={()=>onNavigate('agent')}>进入工作台 →</button><button className="btn" disabled={!canWrite||busy} onClick={()=>void demo()}>{busy?'正在载入…':'载入演示资料'}</button></div></div><div className="hero-flow"><span>01 可信资料</span><i>↓</i><span>02 检索与生成</span><i>↓</i><span>03 人工审核</span><i>↓</i><strong>可追溯的内容资产</strong></div></section>
    {error&&<div role="alert" className="feedback error">{error}</div>}{message&&<div role="status" className="feedback success">{message}</div>}
    <div className="overview-stats"><div><span>知识资料</span><strong>{data?.counts.documents??'—'}</strong></div><div><span>执行任务</span><strong>{data?.counts.runs??'—'}</strong></div><div><span>等待审核</span><strong>{data?.counts.awaiting_review??'—'}</strong></div><div><span>检索方式</span><strong className="stat-text">{status?.mode==='semantic'?'语义检索':status?'词面检索':'连接中'}</strong></div></div>
    <div className="notice-strip">已配置外部模型：{data?.providers.filter(p=>p.configured&&p.provider!=='local').map(p=>p.provider).join(' / ')||'暂未配置，离线演示可用'}<span>创建任务时选择使用哪种模型。演示资料为虚构案例，不代表真实业务效果。</span></div>
    <section className="panel"><div className="section-heading"><div><h2>先体验，再拆解学习</h2><p className="subtle">按完整业务流程理解每一个模块。</p></div></div><div className="learning-grid">{skills.map(([n,title,desc,page,file])=><button key={n} className="learning-card" onClick={()=>onNavigate(page)}><span>{n}</span><h3>{title}</h3><p>{desc}</p><small>{file}</small></button>)}</div></section>
    <section className="panel"><div className="section-heading"><div><h2>深圳岗位参考 · BOSS 直聘</h2><p className="subtle">调研日期 2026-09-19 · 招聘要求用于安排学习和项目验收。</p></div><span className="pill">{data?.jobs.length??0} 条样本</span></div><div className="notice-strip">{data?.jobs_note||'加载调研结果…'}</div>
      <div className="job-grid">{data?.jobs.map(job=><article className="job-card" key={job.id}><div className="section-heading"><strong>{job.title}</strong><span className="pill">{job.detail_body_read?(job.full_jd?'完整正文':'部分正文'):'列表摘要'}</span></div><p>{job.company} · {job.education} · {job.experience}</p><p className="subtle">{Array.isArray(job.responsibilities_summary)?job.responsibilities_summary.join('；'):job.responsibilities_summary}</p>{job.updated_at && <small className="subtle">页面更新时间 {job.updated_at} · 首次发布日期未知</small>}<div className="tags">{job.skill_keywords?.map(k=><span key={k}>{k}</span>)}</div><a href={job.source_url} target="_blank" rel="noreferrer">查看 BOSS 原岗位 ↗</a></article>)}</div>
    </section>
  </div>;
}
