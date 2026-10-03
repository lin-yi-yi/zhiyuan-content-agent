import { useEffect, useState } from 'react';
import { useWorkspace } from '../components/WorkspaceContext';
import { EvidenceCitation, EvidenceCreate, EvidenceNote, EvidenceProvider, EvidenceRights, EvidenceScope, EvidenceSeed, REVIEW_LABELS, evidenceApi, evidenceTime, safeEvidenceUrl } from '../api/evidence';

const blankCitation = (): EvidenceCitation => ({claim:'',excerpt:'',source_url:'',locator:'',product_model:'',parameter:'',value:''});
const blankForm = (): EvidenceCreate => ({title:'',content:'',source_url:'',provider:'manual',version_label:'',expires_at:null,citations:[blankCitation()],rights_basis:'reference_only',rights_note:'',own_note_confirmed:false});
const RIGHTS: Record<EvidenceRights,string> = {own:'自有或本人撰写的内容',permission:'已取得使用许可',reference_only:'仅作参考记录（不可入库）'};
const DEMO: EvidenceCreate = {
  title:'合成示例｜知源资料核验演练', provider:'manual', source_url:'https://example.com/zhiyuan-demo', version_label:'demo-v1',expires_at:null,
  content:'【合成示例，非新闻、非真实产品说明】这是本项目自编的核验流程练习。虚构产品「知源示例设备 A」的演练保修期为 24 个月，资料版本为 demo-v1。这个参数只用于演示资料引用、人工核验、独立入库和撤销后的检索行为，不能用于采购、对外宣传或实际售后承诺。example.com 地址只是示例标识，不提供上述产品资料。',
  citations:[{claim:'演练设定中，知源示例设备 A 的保修期为 24 个月。',excerpt:'虚构产品「知源示例设备 A」的演练保修期为 24 个月，资料版本为 demo-v1。',source_url:'https://example.com/zhiyuan-demo',locator:'本页合成示例正文；网址仅为示例标识',product_model:'知源示例设备 A',parameter:'保修期',value:'24 个月'}],
  rights_basis:'own',rights_note:'项目自编的合成演示文字，不是转载新闻或真实产品承诺。',own_note_confirmed:false,
};

function NoteLink({url}:{url:string}) {
  const href=safeEvidenceUrl(url);
  return href?<a href={href} target="_blank" rel="noopener noreferrer">{url} ↗</a>:<span>{url||'未登记来源'}</span>;
}
function localInput(iso:string|null):string {
  if(!iso)return '';
  const date=new Date(iso);if(Number.isNaN(date.getTime()))return '';
  return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);
}

interface Props { scope?:EvidenceScope; seed?:EvidenceSeed|null; initialNoteId?:number|null; onSeedConsumed?:()=>void; onNavigate?:(page:string)=>void }
export default function EvidencePage({scope,seed,initialNoteId,onSeedConsumed,onNavigate}:Props) {
  const {canWrite,canReview}=useWorkspace();
  const [notes,setNotes]=useState<EvidenceNote[]>([]);
  const [selected,setSelected]=useState<EvidenceNote|null>(null);
  const [form,setForm]=useState<EvidenceCreate>(blankForm);
  const [compose,setCompose]=useState(true);
  const [busy,setBusy]=useState(false);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');
  const [message,setMessage]=useState('');
  const [confirmed,setConfirmed]=useState(false);
  const [reviewNote,setReviewNote]=useState('');
  const [demo,setDemo]=useState(false);

  useEffect(()=>{let active=true;setLoading(true);evidenceApi.list(scope).then(async result=>{if(!active)return;setNotes(result.items);if(initialNoteId){const match=result.items.find(n=>n.id===initialNoteId)||await evidenceApi.get(initialNoteId,scope);if(active){setSelected(match);setCompose(false);}}}).catch(e=>{if(active)setError(e instanceof Error?e.message:String(e));}).finally(()=>{if(active)setLoading(false);});return()=>{active=false;};},[initialNoteId,scope?.workspace_id,scope?.knowledge_base_id]);
  useEffect(()=>{if(!seed)return;setForm({...blankForm(),...seed});setSelected(null);setCompose(true);setDemo(false);setError('');setMessage('已带入标题和来源链接，请阅读原文后撰写自己的核验笔记。');onSeedConsumed?.();},[seed,onSeedConsumed]);

  const choose=(note:EvidenceNote)=>{setSelected(note);setCompose(false);setConfirmed(false);setReviewNote('');setError('');setMessage('');setDemo(false);};
  const start=(value=blankForm(),isDemo=false)=>{setForm(value);setSelected(null);setCompose(true);setConfirmed(false);setReviewNote('');setDemo(isDemo);setError('');setMessage('');};
  const apply=(note:EvidenceNote)=>{setSelected(note);setCompose(false);setConfirmed(false);setNotes(old=>[note,...old.filter(item=>item.id!==note.id)]);};
  const act=async(fn:()=>Promise<EvidenceNote>,success:string)=>{setBusy(true);setError('');setMessage('');try{apply(await fn());setMessage(success);}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}};
  const changeCitation=(index:number,key:keyof EvidenceCitation,value:string)=>setForm(old=>({...old,citations:old.citations.map((item,i)=>i===index?{...item,[key]:value}:item)}));
  const save=()=>{
    if(!form.title.trim()||Array.from(form.content.trim()).length<40){setError('请填写标题和至少 40 个字的本人笔记。');return;}
    if(!safeEvidenceUrl(form.source_url)){setError('请填写有效的 HTTP(S) 来源链接，不含用户名或密码。');return;}
    const citations=form.citations.filter(item=>item.claim.trim()||item.excerpt.trim()||item.source_url.trim()||item.product_model?.trim()||item.parameter?.trim()||item.value?.trim());
    if(citations.some(item=>!item.claim.trim()||!item.excerpt.trim()||!safeEvidenceUrl(item.source_url))){setError('每条引用都需填写待核对结论、对应摘录和有效来源链接；暂不填写的引用可移除。');return;}
    if(citations.some(item=>(item.product_model?.trim()||item.parameter?.trim()||item.value?.trim())&&(!item.product_model?.trim()||!item.parameter?.trim()||!item.value?.trim()||!item.locator?.trim()||!form.version_label.trim()))){setError('产品参数需同时填写品牌及型号、参数名、参数值、原文定位和来源版本。请将对应摘录保留在笔记正文中。');return;}
    if(form.provider==='aihot'&&!form.own_note_confirmed){setError('请确认正文是阅读来源后自行撰写的笔记，不是复制 AIHOT 摘要。');return;}
    void act(()=>evidenceApi.create({...form,title:form.title.trim(),content:form.content.trim(),source_url:form.source_url.trim(),citations},scope),'笔记已保存为待核验。人工确认与入库需要分别操作。');
  };
  const activeScope=selected?{workspace_id:selected.workspace_id,knowledge_base_id:selected.knowledge_base_id}:scope;
  const displayedScope=activeScope||notes[0];
  const expired=Boolean(selected?.is_expired);
  const canVerify=Boolean(canReview&&selected?.review_status==='pending'&&!expired&&selected.rights_basis!=='reference_only'&&selected.citations.length&&(selected.rights_basis!=='permission'||selected.rights_note.trim()));
  const canIndex=Boolean(canReview&&selected?.review_status==='verified'&&!expired&&selected.rights_basis!=='reference_only'&&selected.index_status!=='indexed');

  return <div className="evidence-page">
    <div className="page-header source-hub-heading"><div><h1>核验笔记</h1><p>记录自己核对过的事实，人工确认后，再单独加入知识库。</p></div><div className="form-actions"><button className="btn" disabled={!canWrite||busy} onClick={()=>start({...DEMO,citations:DEMO.citations.map(c=>({...c}))},true)}>填入合成示例</button><button className="btn btn-primary" disabled={!canWrite||busy} onClick={()=>start()}>新建笔记</button></div></div>
    <div className="notice-strip">{displayedScope?`工作区 #${displayedScope.workspace_id} · 知识库 #${displayedScope.knowledge_base_id}`:'默认工作区 · 默认知识库'}<span>当前列表、新建笔记、核验和入库均使用此知识库。</span>{scope&&onNavigate&&<button className="btn btn-sm" disabled={busy} onClick={()=>onNavigate('evidence')}>返回默认知识库笔记</button>}</div>
    <div className="evidence-flow"><span>1　保存本人笔记</span><span>2　人工核对来源</span><span>3　确认加入知识库</span><span>4　回答查看引用</span></div>
    {error&&<div className="feedback error" role="alert">{error}</div>}{message&&<div className="feedback success" role="status">{message}</div>}
    <div className="evidence-layout">
      <aside className="evidence-list"><div className="section-heading"><h2>笔记记录</h2><span>{notes.length} 份</span></div>{loading?<p className="subtle">正在读取笔记…</p>:!notes.length?<p className="subtle">从信源台带入链接，或新建一份笔记。合成示例也需要你检查后逐步保存。</p>:notes.map(note=><button key={note.id} className={`evidence-list-item ${selected?.id===note.id&&!compose?'active':''}`} disabled={busy} onClick={()=>choose(note)}><strong>{note.title}</strong><span className={`evidence-badge ${note.review_status}`}>{REVIEW_LABELS[note.review_status]}</span><small>{note.is_expired?'已过期 · 不可入库':note.index_status==='indexed'?'已入知识库':note.index_status==='stale'?'原索引已失效':'未入库'} · {note.version_label||'未登记版本'}</small></button>)}</aside>
      <section className="evidence-editor">
        {compose?<form onSubmit={event=>{event.preventDefault();save();}}>
          <h2>填写自己的核验笔记</h2><p className="subtle">正文保存后不直接修改。修订时先撤销旧版核验，再另存为一份待核验笔记。</p>
          {demo&&<div className="notice-strip">合成示例，不是新闻或真实产品资料。<span>以下文字由项目自编；example.com 仅作示例标识。填入不会自动保存、核验或入库。</span></div>}
          <fieldset disabled={!canWrite||busy}>
            <div className="form-group"><label htmlFor="evidence-title">标题</label><input id="evidence-title" maxLength={200} value={form.title} onChange={e=>setForm({...form,title:e.target.value})} required/></div>
            <div className="form-row"><div className="form-group"><label htmlFor="evidence-provider">发现渠道</label><select id="evidence-provider" value={form.provider} onChange={e=>setForm({...form,provider:e.target.value as EvidenceProvider,own_note_confirmed:false})}><option value="manual">本人录入</option><option value="github">GitHub</option><option value="aihot">AIHOT</option></select></div><div className="form-group"><label htmlFor="evidence-version">来源版本</label><input id="evidence-version" maxLength={100} value={form.version_label} onChange={e=>setForm({...form,version_label:e.target.value})} placeholder="例如 v1.2.0 / 2026-09 版"/></div></div>
            <div className="form-group"><label htmlFor="evidence-url">主要来源链接</label><input id="evidence-url" type="url" value={form.source_url} onChange={e=>setForm({...form,source_url:e.target.value})} placeholder="https://…" required/></div>
            <div className="form-group"><label htmlFor="evidence-content">本人笔记（至少 40 字）</label><textarea id="evidence-content" rows={8} maxLength={120000} value={form.content} onChange={e=>setForm({...form,content:e.target.value})} placeholder="阅读原文后，用自己的话记录结论、适用条件和不确定性。不要粘贴聚合平台摘要。" required/><small className="subtle">已填写 {Array.from(form.content.trim()).length} 字</small></div>
            <div className="form-row"><div className="form-group"><label htmlFor="evidence-rights">内容使用依据</label><select id="evidence-rights" value={form.rights_basis} onChange={e=>setForm({...form,rights_basis:e.target.value as EvidenceRights})}>{Object.entries(RIGHTS).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></div><div className="form-group"><label htmlFor="evidence-expires">到期时间（本地时间，可选）</label><input id="evidence-expires" type="datetime-local" value={localInput(form.expires_at)} onChange={e=>setForm({...form,expires_at:e.target.value?new Date(e.target.value).toISOString():null})}/></div></div>
            <div className="form-group"><label htmlFor="evidence-rights-note">使用依据说明{form.rights_basis==='permission'?'（取得许可时必填）':''}</label><input id="evidence-rights-note" value={form.rights_note} onChange={e=>setForm({...form,rights_note:e.target.value})} placeholder="本人研究笔记，或许可方与允许使用的范围"/></div>
            <div className="section-heading"><div><h3>逐条核对的引用</h3><p className="subtle">结论与摘录一一对应；可添加不同来源。没有引用可先保存，不能直接确认核验。</p><p className="subtle">工业产品 FAQ 请填写结构化参数并保留原文摘录。产品名含品牌与型号；参数名写明适用条件（例如“额定电压 / 直流输入”）。一个品牌优先使用独立知识库。同范围、同产品及参数的不同值会阻止核验；单位换算、别名和未结构化文字仍需人工检查。</p></div><button type="button" className="btn" onClick={()=>setForm({...form,citations:[...form.citations,blankCitation()]})}>添加引用</button></div>
            {form.citations.map((citation,index)=><div className="evidence-citation-edit" key={index}><div className="section-heading"><strong>引用 {index+1}</strong><button type="button" className="btn btn-text" onClick={()=>setForm({...form,citations:form.citations.filter((_,i)=>i!==index)})}>移除</button></div><div className="form-row"><div className="form-group"><label htmlFor={`product-${index}`}>品牌及产品型号（产品参数时填写）</label><input id={`product-${index}`} maxLength={200} value={citation.product_model||''} onChange={e=>changeCitation(index,'product_model',e.target.value)} placeholder="例如 合成星桥 XP-24"/></div><div className="form-group"><label htmlFor={`parameter-${index}`}>参数名及适用条件</label><input id={`parameter-${index}`} maxLength={200} value={citation.parameter||''} onChange={e=>changeCitation(index,'parameter',e.target.value)} placeholder="例如 额定电压 / 直流输入"/></div><div className="form-group"><label htmlFor={`value-${index}`}>参数值（含单位）</label><input id={`value-${index}`} maxLength={500} value={citation.value||''} onChange={e=>changeCitation(index,'value',e.target.value)} placeholder="例如 24 V"/></div></div><div className="form-group"><label htmlFor={`claim-${index}`}>需要核对的结论</label><textarea id={`claim-${index}`} rows={2} value={citation.claim} onChange={e=>changeCitation(index,'claim',e.target.value)}/></div><div className="form-group"><label htmlFor={`excerpt-${index}`}>支持该结论的原文摘录</label><textarea id={`excerpt-${index}`} rows={3} value={citation.excerpt} onChange={e=>changeCitation(index,'excerpt',e.target.value)}/></div><div className="form-group"><label htmlFor={`citation-url-${index}`}>该条证据的来源链接</label><input id={`citation-url-${index}`} type="url" value={citation.source_url} onChange={e=>changeCitation(index,'source_url',e.target.value)}/></div><div className="form-group"><label htmlFor={`locator-${index}`}>定位说明（章节、段落或页码）</label><input id={`locator-${index}`} value={citation.locator||''} onChange={e=>changeCitation(index,'locator',e.target.value)}/></div></div>)}
            {form.provider==='aihot'&&<label className="evidence-check"><input type="checkbox" checked={form.own_note_confirmed} onChange={e=>setForm({...form,own_note_confirmed:e.target.checked})}/><span>正文是我阅读来源后自行撰写的笔记，没有复制 AIHOT 摘要作为知识正文。</span></label>}
            <button className="btn btn-primary" type="submit">{busy?'保存中…':'保存为待核验笔记'}</button>
          </fieldset>
        </form>:selected&&<article>
          <div className="section-heading"><span className={`evidence-badge ${selected.review_status}`}>{REVIEW_LABELS[selected.review_status]}</span><span className="pill">#{selected.id} · {selected.index_status==='indexed'?'已入库':selected.index_status==='stale'?'索引已失效':'未入库'}</span></div>
          <h2>{selected.title}</h2><p className="subtle"><NoteLink url={selected.source_url}/></p>
          <dl className="source-hub-metadata"><div><dt>版本</dt><dd>{selected.version_label||'未登记'}</dd></div><div><dt>到期时间</dt><dd>{evidenceTime(selected.expires_at)}{expired?' · 已过期':''}</dd></div><div><dt>使用依据</dt><dd>{RIGHTS[selected.rights_basis]}{selected.rights_note&&` · ${selected.rights_note}`}</dd></div><div><dt>核验时间</dt><dd>{evidenceTime(selected.verified_at||selected.reviewed_at)}</dd></div></dl>
          {expired&&<div className="feedback error">这份笔记已过期，不能加入知识库。请核对新版本后新建笔记。</div>}
          {Boolean(selected.fact_conflict_note_ids?.length)&&<div className="feedback error">结构化事实与笔记 {selected.fact_conflict_note_ids?.map(id=>`#${id}`).join('、')} 存在冲突；请核对产品、参数及适用条件，撤销旧版后重新核验。系统不会自动选择其中一个值。</div>}{selected.rights_basis==='reference_only'&&<div className="notice-strip">仅作参考记录，不具备本流程的入库条件。</div>}
          <h3>本人笔记</h3><p className="evidence-body">{selected.content}</p><h3>来源与逐条引用</h3>{!selected.citations.length&&<p className="subtle">没有登记引用，不能确认核验。</p>}
          {selected.citations.map((citation,index)=><div className="evidence-citation-view" key={index}><strong>{index+1}. {citation.claim}</strong>{citation.product_model&&<p className="subtle">{citation.product_model} · {citation.parameter}：{citation.value} · 资料版本 {selected.version_label}</p>}<blockquote>{citation.excerpt}</blockquote><NoteLink url={citation.source_url}/>{citation.locator&&<small>{citation.locator}</small>}</div>)}
          <section className="evidence-review"><h3>人工核验</h3><p className="subtle">这里记录你自己的核验决定，系统不会自动判断结论真实。确认核验不会自动入库。</p>
            {selected.review_status==='pending'&&<><label className="evidence-check"><input type="checkbox" disabled={busy||!canVerify} checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/><span>我已查看所列来源，核对结论、摘录及适用条件，并确认内容的使用依据。</span></label><label htmlFor="evidence-review-note">核验说明／未通过原因</label><textarea id="evidence-review-note" rows={2} value={reviewNote} disabled={busy} onChange={e=>setReviewNote(e.target.value)}/><div className="form-actions"><button className="btn btn-primary" disabled={busy||!confirmed||!canVerify} onClick={()=>void act(()=>evidenceApi.review(selected.id,'verify',confirmed,reviewNote,activeScope),'人工核验已记录。请继续单独点击“加入知识库”。')}>确认已核验</button><button className="btn" disabled={!canReview||busy} onClick={()=>void act(()=>evidenceApi.review(selected.id,'reject',false,reviewNote,activeScope),'已记录未通过核验。')}>未通过核验</button></div></>}
            {selected.review_status==='verified'&&<div className="form-actions"><button className="btn btn-primary" disabled={busy||!canIndex} onClick={()=>void act(async()=>{const result=await evidenceApi.index(selected.id,activeScope);return result.note;},'笔记已加入知识库，可以在检索与评测中查看引用。')}>{selected.index_status==='indexed'?'已加入知识库':'加入知识库'}</button><button className="btn" disabled={!canReview||busy} onClick={()=>void act(()=>evidenceApi.review(selected.id,'revoke',false,'本人撤销核验；相关知识索引应停用。',activeScope),'已撤销核验，相关知识索引不再作为有效证据。')}>撤销核验</button></div>}
            {selected.review_note&&<p className="subtle">核验说明：{selected.review_note}</p>}
            <div className="form-actions evidence-followup"><button className="btn" disabled={!canWrite||busy||selected.review_status==='verified'} onClick={()=>start({...blankForm(),title:selected.title,content:selected.content,source_url:selected.source_url,provider:selected.provider,version_label:selected.version_label,expires_at:selected.expires_at,citations:selected.citations.map(c=>({...c})),rights_basis:selected.rights_basis,rights_note:selected.rights_note})}>以此新建修订</button>{selected.review_status==='verified'&&<small>新建修订前先撤销旧版核验。</small>}{selected.index_status==='indexed'&&onNavigate&&<button className="btn" onClick={()=>onNavigate('rag')}>前往检索与评测</button>}</div>
          </section>
        </article>}
      </section>
    </div>
  </div>;
}
