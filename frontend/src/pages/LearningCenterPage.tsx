import { useEffect, useRef, useState } from 'react';
import { LearningCatalog, LearningQuestion, SourceExcerpt, learningApi } from '../api/learning';
import { useWorkspace } from '../components/WorkspaceContext';
import { LearningProgress, Mastery, learningStorageKey, parseLearningProgress, updateLearningAnswer, updateLearningLab, updateLearningRating } from '../utils/learningProgress';
import '../styles/learning.css';

interface Props { initialModule?:string; onNavigate?:(page:string)=>void }
const LEVELS:{value:Mastery;label:string}[]=[{value:'new',label:'还不会'},{value:'learning',label:'在学习'},{value:'explain',label:'能解释'},{value:'demonstrate',label:'能演示排错'}];
type Tab='explain'|'interview'|'practice';
const TABS:{id:Tab;title:string}[]=[{id:'explain',title:'看懂原理'},{id:'interview',title:'面试自测'},{id:'practice',title:'动手验证'}];

export default function LearningCenterPage(props:Props) {
  const {session,organization}=useWorkspace();
  const storageKey=learningStorageKey({mode:session.mode,userId:session.user?.id,organizationId:organization?.id});
  // A scope change remounts all private answers and self-assessments immediately.
  return <LearningCenter key={storageKey} {...props} storageKey={storageKey}/>;
}

function LearningCenter({initialModule,onNavigate,storageKey}:Props & {storageKey:string}) {
  const [catalog,setCatalog]=useState<LearningCatalog|null>(null);
  const [error,setError]=useState('');
  const [retry,setRetry]=useState(0);
  const [moduleId,setModuleId]=useState(initialModule || 'frontend');
  const [tab,setTab]=useState<Tab>('explain');
  const [query,setQuery]=useState('');
  const [expanded,setExpanded]=useState<Set<string>>(new Set());
  const [storageWarning,setStorageWarning]=useState('');
  const [progress,setProgress]=useState<LearningProgress>(()=>{
    try{return parseLearningProgress(localStorage.getItem(storageKey));}catch{return parseLearningProgress(null);}
  });
  const [sourceId,setSourceId]=useState<string|null>(null);
  const [source,setSource]=useState<SourceExcerpt|null>(null);
  const [sourceError,setSourceError]=useState('');
  const sourceDialog=useRef<HTMLDialogElement>(null);
  const tabsRef=useRef<HTMLDivElement>(null);

  useEffect(()=>{
    let active=true;
    setError('');
    learningApi.catalog().then(data=>{if(active)setCatalog(data);}).catch(e=>{if(active && e.name!=='AbortError')setError(e.message || '课程暂时无法读取');});
    return ()=>{active=false;};
  },[retry]);

  useEffect(()=>{if(initialModule){setModuleId(initialModule);setTab('explain');setQuery('');}},[initialModule]);
  useEffect(()=>{
    if(!sourceId)return;
    let active=true;
    setSource(null);setSourceError('');
    const dialog=sourceDialog.current;
    if(dialog && !dialog.open)dialog.showModal();
    learningApi.source(sourceId).then(data=>{if(active)setSource(data);}).catch(e=>{if(active && e.name!=='AbortError')setSourceError(e.message || '暂时无法读取源码');});
    return ()=>{active=false;dialog?.close();};
  },[sourceId]);

  function save(next:LearningProgress) {
    setProgress(next);
    try{localStorage.setItem(storageKey,JSON.stringify(next));setStorageWarning('');}
    catch{setStorageWarning('浏览器存储不可用，本次修改只能保留到页面关闭。');}
  }
  function selectModule(id:string) {setModuleId(id);setQuery('');setExpanded(new Set());}
  function toggleAnswer(id:string) {setExpanded(current=>{const next=new Set(current);if(next.has(id))next.delete(id);else next.add(id);return next;});}
  function renderSources(ids:string[]) {
    return <div className="learn-source-links" aria-label="对应项目源码">{ids.map(id=>{
      const file=catalog?.sources.find(item=>item.id===id)?.path || id;
      return <button key={id} type="button" className="learn-source-link" onClick={()=>{setSource(null);setSourceError('');setSourceId(id);}} title={file}>查看 {file.split('/').pop()}</button>;
    })}</div>;
  }
  function renderQuestion(question:LearningQuestion,moduleTitle?:string) {
    const open=expanded.has(question.id);
    return <article className="learn-question" key={question.id}>
      <div className="learn-question-heading"><div>{moduleTitle && <span className="learn-eyebrow">{moduleTitle}</span>}<h3>{question.title}</h3></div><span className="learn-mastery-label">{LEVELS.find(level=>level.value===(progress.ratings[question.id] || 'new'))?.label}</span></div>
      <label className="learn-answer-input">先用自己的话回答（可选，仅本机保存）<textarea maxLength={2000} rows={2} value={progress.answers[question.id] || ''} onChange={event=>save(updateLearningAnswer(progress,question.id,event.target.value))} placeholder="说清原理，再举这个项目中的例子。"/></label>
      <button type="button" className="btn btn-sm" aria-expanded={open} aria-controls={`answer-${question.id}`} onClick={()=>toggleAnswer(question.id)}>{open?'收起参考答案':'对照参考答案'}</button>
      {open && <div className="learn-answer" id={`answer-${question.id}`}><p>{question.answer}</p><div className="learn-followups"><strong>面试官可能继续问</strong><ul>{question.followups.map(item=><li key={item}>{item}</li>)}</ul></div><p className="learn-pitfall"><strong>常见误区：</strong>{question.pitfall}</p>{renderSources(question.source_ids)}</div>}
      <fieldset className="learn-rating"><legend>我的自测</legend>{LEVELS.map(level=><label key={level.value} className={(progress.ratings[question.id] || 'new')===level.value?'selected':''}><input type="radio" name={`mastery-${question.id}`} checked={(progress.ratings[question.id] || 'new')===level.value} onChange={()=>save(updateLearningRating(progress,question.id,level.value))}/>{level.label}</label>)}</fieldset>
    </article>;
  }

  if(error)return <section className="learning-center"><div className="learn-empty" role="alert"><h1>课程暂时无法读取</h1><p>{error}</p><button className="btn" onClick={()=>setRetry(value=>value+1)}>重新加载</button></div></section>;
  if(!catalog)return <section className="learning-center"><div className="learn-empty" role="status">正在准备你的项目学习路线…</div></section>;
  const current=catalog.modules.find(item=>item.id===moduleId) || catalog.modules[0];
  if(!current)return <section className="learning-center"><div className="learn-empty">当前没有可用课程。</div></section>;
  const allQuestions=catalog.modules.flatMap(module=>module.questions.map(question=>({question,module})));
  const allLabs=catalog.modules.flatMap(module=>module.labs);
  const known=allQuestions.filter(({question})=>['explain','demonstrate'].includes(progress.ratings[question.id])).length;
  const demonstrated=allQuestions.filter(({question})=>progress.ratings[question.id]==='demonstrate').length;
  const finishedLabs=allLabs.filter(lab=>progress.labs[lab.id]).length;
  const search=query.trim().toLocaleLowerCase();
  const questions=search ? allQuestions.filter(({question,module})=>[question.title,question.answer,question.pitfall,...question.followups,module.title,...module.technologies].join(' ').toLocaleLowerCase().includes(search)) : current.questions.map(question=>({question,module:current}));
  const nextModule=catalog.modules.find(module=>module.order===current.order+1);
  return <section className="learning-center">
    <header className="learn-header"><div><p className="learn-eyebrow">项目学习中心</p><h1>{catalog.title}</h1><p>选一个模块，先解释，再修改，最后用结果证明。</p></div><div className="learn-summary" aria-label="本机学习进度"><strong>{known}<small> / {allQuestions.length} 题能解释</small></strong><span>{demonstrated} 题能演示 · {finishedLabs}/{allLabs.length} 项练习已自测</span></div></header>
    <div className="learn-progress-track" role="progressbar" aria-label="能解释的题目比例" aria-valuenow={known} aria-valuemin={0} aria-valuemax={allQuestions.length}><span style={{width:`${known/allQuestions.length*100}%`}}/></div>
    <p className="learn-local-notice">{catalog.progress_notice}</p>
    {storageWarning && <p className="learn-warning" role="status">{storageWarning}</p>}
    <details className="learn-stack-map"><summary>查看整套技术栈 · {catalog.modules.length} 个模块</summary><div>{catalog.modules.map(module=><button type="button" key={module.id} onClick={()=>{selectModule(module.id);setTab('explain');}}><strong>{module.title}</strong><span>{module.technologies.join(' · ')}</span></button>)}</div><p>点击模块进入实现解释；先进技术是否已经接入，需结合“项目已经做到”和“还不能这样宣称”一起看。</p></details>
    <div className="learn-layout">
      <nav className="learn-modules" aria-label="学习模块"><p className="learn-nav-title">建议学习顺序</p>{catalog.modules.map(module=>{
        const count=module.questions.filter(question=>['explain','demonstrate'].includes(progress.ratings[question.id])).length;
        return <button key={module.id} type="button" aria-current={current.id===module.id?'step':undefined} className={`learn-module-button ${current.id===module.id?'active':''}`} onClick={()=>selectModule(module.id)}><span className="learn-module-number">{module.order}</span><span><strong>{module.title}</strong><small>{count}/{module.questions.length} 题能解释</small></span></button>;
      })}</nav>
      <div className="learn-content">
        <div className="learn-module-intro"><p className="learn-eyebrow">MODULE {String(current.order).padStart(2,'0')}</p><h2>{current.title}</h2><p>{current.subtitle}</p><div className="learn-tech-tags">{current.technologies.map(tech=><span key={tech}>{tech}</span>)}</div></div>
        <div className="learn-tabs" role="tablist" aria-label="学习方式" ref={tabsRef} onKeyDown={event=>{
          if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
          event.preventDefault();const index=TABS.findIndex(item=>item.id===tab);
          const next=event.key==='Home'?0:event.key==='End'?TABS.length-1:(index+(event.key==='ArrowRight'?1:-1)+TABS.length)%TABS.length;
          setTab(TABS[next].id);tabsRef.current?.querySelectorAll<HTMLButtonElement>('[role=tab]')[next].focus();
        }}>{TABS.map(item=><button key={item.id} type="button" id={`learn-tab-${item.id}`} role="tab" aria-selected={tab===item.id} aria-controls={`learn-panel-${item.id}`} tabIndex={tab===item.id?0:-1} onClick={()=>setTab(item.id)}>{item.title}{item.id==='interview' && <span>{current.questions.length}</span>}</button>)}</div>
        <div className="learn-panel" role="tabpanel" id={`learn-panel-${tab}`} aria-labelledby={`learn-tab-${tab}`}>
          {tab==='explain' && <>
            <div className="learn-explanation"><p className="learn-lead">{current.summary}</p>{current.explanations.map((text,index)=><div className="learn-concept" key={text}><span>{index+1}</span><p>{text}</p></div>)}</div>
            <div className="learn-boundaries"><div><h3>项目已经做到</h3><p>{current.implemented}</p></div><div><h3>还不能这样宣称</h3><p>{current.limitations}</p></div></div>
            <div className="learn-reading"><h3>从真实代码开始</h3><p>打开对应片段，先找输入、输出和失败出口。</p>{renderSources(Array.from(new Set(current.questions.flatMap(question=>question.source_ids))).slice(0,4))}</div>
            <div className="learn-docs"><h3>官方资料</h3>{current.docs.map(doc=><a href={doc.url} target="_blank" rel="noreferrer" key={doc.url}>{doc.title} <span aria-hidden="true">↗</span></a>)}<p>官方文档用于理解概念；项目采用的实现与边界以上面的代码为准。</p></div>
            <div className="learn-section-action"><button className="btn btn-primary" onClick={()=>setTab('interview')}>用 {current.questions.length} 道题检查理解</button></div>
          </>}
          {tab==='interview' && <><label className="learn-search-label">搜索全部 {allQuestions.length} 道题<input type="search" value={query} onChange={event=>setQuery(event.target.value)} placeholder="例如：事务、幻觉、MCP、组织隔离"/></label><div className="learn-question-count"><span>{search?`找到 ${questions.length} 道相关题`:`本模块 ${questions.length} 道题 · 参考答案默认隐藏`}</span>{expanded.size>0 && <button className="learn-text-button" onClick={()=>setExpanded(new Set())}>收起全部答案</button>}</div>{questions.length?questions.map(({question,module})=>renderQuestion(question,search?module.title:undefined)):<div className="learn-empty">没有找到相关题目，试试更短的关键词。</div>}</>}
          {tab==='practice' && <><p className="learn-lead">每项练习都要留下“改了什么、哪里坏了、如何证明修好”的记录。故意改坏只在练习分支、临时数据或模拟请求中进行。</p>{current.labs.map(lab=><article className="learn-lab" key={lab.id}><div className="learn-question-heading"><h3>{lab.title}</h3><span>约 {lab.minutes} 分钟</span></div><ol>{lab.steps.map(step=><li key={step.label}><strong>{step.label}</strong><p>{step.text}</p></li>)}</ol>{renderSources(lab.source_ids)}<div className="learn-lab-footer"><label><input type="checkbox" checked={!!progress.labs[lab.id]} onChange={event=>save(updateLearningLab(progress,lab.id,event.target.checked))}/>我已按验收条件完成自测</label>{onNavigate && <button className="btn btn-sm" onClick={()=>onNavigate(lab.page)}>打开对应功能</button>}</div></article>)}{nextModule && <div className="learn-section-action"><button className="btn" onClick={()=>{selectModule(nextModule.id);setTab('explain');}}>下一模块：{nextModule.title}</button></div>}</>}
        </div>
        <details className="learn-frontier"><summary>学会当前实现后，再研究哪些技术？</summary><div>{catalog.next_technologies.filter(item=>item.module===current.id).length ? catalog.next_technologies.filter(item=>item.module===current.id).map(item=><article key={item.name}><span>{item.status}</span><h3>{item.name}</h3><p>{item.reason}</p></article>) : <p>先完成当前模块的解释、修改与排错。后续可从“可信知识问答”和“AI 内容工作流”查看进阶方向。</p>}</div></details>
      </div>
    </div>
    <p className="learn-footer">{catalog.notice}</p>
    {sourceId && <dialog className="learn-source-dialog" ref={sourceDialog} aria-labelledby="learn-source-title" onCancel={()=>setSourceId(null)} onClose={()=>setSourceId(null)}><header><div><p className="learn-eyebrow">项目源码 · 只读</p><h2 id="learn-source-title">{source?.path || catalog.sources.find(item=>item.id===sourceId)?.path || '加载源码'}</h2></div><button className="btn btn-sm" onClick={()=>setSourceId(null)} autoFocus>关闭</button></header>{sourceError?<p role="alert" className="learn-warning">{sourceError}</p>:source?<><p>{source.notice}</p><pre aria-label={`源码第 ${source.start_line} 至 ${source.end_line} 行`}><code>{source.code.split('\n').map((line,index)=><span className="learn-code-line" key={index}><span className="learn-line-number" aria-hidden="true">{source.start_line+index}</span><span>{line || ' '}</span></span>)}</code></pre></>:<p role="status">正在读取固定源码片段…</p>}</dialog>}
  </section>;
}
