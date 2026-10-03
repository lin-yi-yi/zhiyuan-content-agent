import {useEffect,useState} from 'react';
import {api,KnowledgeBase} from '../api/client';
import {BrandInput,BrandProfile,brandsApi} from '../api/brands';
import {useWorkspace} from '../components/WorkspaceContext';
import {brandKnowledgeRoute} from '../utils/navigation';
import '../styles/brands.css';

const blank:BrandInput={name:'',audience:'',tone:'专业、清楚，避免夸张承诺',prohibited_claims:'',call_to_action:'',knowledge_base_id:0,data_policy:'cloud_allowed',is_active:true};
export default function BrandsPage({onNavigate,initialBrandId}:{onNavigate:(page:string)=>void;initialBrandId?:number|null}){
  const {canWrite}=useWorkspace();
  const [brands,setBrands]=useState<BrandProfile[]>([]),[libraries,setLibraries]=useState<KnowledgeBase[]>([]);
  const [selected,setSelected]=useState<BrandProfile|null>(null),[form,setForm]=useState<BrandInput>({...blank});
  const [busy,setBusy]=useState(false),[ready,setReady]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState(''),[archived,setArchived]=useState(false);
  const edit=(item:BrandProfile)=>{setSelected(item);setForm({...item});setError('');setMessage('');};
  const refresh=async()=>{setError('');try{const [items,kbs]=await Promise.all([brandsApi.list(),api.listKnowledgeBases()]);setBrands(items);setLibraries(kbs);setForm(old=>({...old,knowledge_base_id:old.knowledge_base_id||kbs[0]?.id||0}));setReady(true);if(selected){const current=items.find(b=>b.id===selected.id);if(current)edit(current);else setError('该品牌已不可用，请新建或选择其他档案。');}}catch(e){setError(e instanceof Error?e.message:'无法读取品牌档案');}};
  useEffect(()=>{let active=true;Promise.all([brandsApi.list(),api.listKnowledgeBases()]).then(([items,kbs])=>{if(!active)return;setBrands(items);setLibraries(kbs);setForm(old=>({...old,knowledge_base_id:kbs[0]?.id||0}));setReady(true);if(initialBrandId){const item=items.find(b=>b.id===initialBrandId);if(item)edit(item);else setError('指定品牌不可用，请重新选择。');}}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[]);
  const create=()=>{setSelected(null);setForm({...blank,knowledge_base_id:libraries[0]?.id||0});setError('');setMessage('');};
  const save=async(event:React.FormEvent)=>{event.preventDefault();if(busy)return;setBusy(true);setError('');setMessage('');try{
    const body={...form,name:form.name.trim(),workspace_id:libraries.find(k=>k.id===form.knowledge_base_id)?.workspace_id};
    const saved=selected?await brandsApi.update(selected.id,{...body,version:selected.version}):await brandsApi.create(body);
    setBrands(old=>[saved,...old.filter(item=>item.id!==saved.id)]);setSelected(saved);setForm({...saved});setMessage(`已保存「${saved.name}」第 ${saved.version} 版。新任务会使用这版要求。`);
  }catch(e){setError(e instanceof Error?e.message:'保存失败');}finally{setBusy(false);}};
  const visible=brands.filter(item=>archived||item.is_active);
  return <div className="brand-page">
    <div className="page-header compact-page-heading"><div><span className="eyebrow">BRAND WORKSPACE</span><h1>让每份内容，都符合品牌。</h1><p>把受众、表达习惯和禁区说明一次，创建任务时直接使用。</p></div><button className="btn" disabled={!canWrite||busy} onClick={create}>＋ 新建品牌</button></div>
    {error&&<div className="feedback error" role="alert">{error} <button className="text-action" onClick={()=>void refresh()}>{selected?'重新载入最新档案（覆盖未保存编辑）':'重新读取'}</button></div>}
    {message&&<div className="notice-strip" role="status">{message}</div>}
    <div className="brand-columns"><aside className="panel brand-list"><div className="section-heading"><h2>品牌档案</h2><span className="subtle">{brands.filter(b=>b.is_active).length} 个在用</span></div>
      {!ready&&!error?<p className="empty">正在读取…</p>:!visible.length?<div className="brand-empty"><strong>先建立你的第一个品牌</strong><p>可以是自己的账号，也可以是正在服务的客户。</p></div>:visible.map(item=><button key={item.id} className={`brand-list-item ${selected?.id===item.id?'active':''}`} onClick={()=>edit(item)} disabled={busy}><span className="brand-avatar">{item.name.slice(0,1)}</span><span><strong>{item.name}</strong><small>{item.is_active?`第 ${item.version} 版 · ${libraries.find(k=>k.id===item.knowledge_base_id)?.name||'关联资料库'}`:'已归档'}</small></span></button>)}
      <label className="inline-choice"><input type="checkbox" checked={archived} onChange={e=>setArchived(e.target.checked)}/>显示已归档品牌</label><p className="subtle">同一组织成员共享这些档案。需要隔离客户访问权限时，请使用独立组织。</p>
    </aside><section className="panel brand-editor"><div className="section-heading"><div><h2>{selected?'编辑品牌要求':'新建品牌档案'}</h2><p className="subtle">品牌要求决定写法；产品事实仍需来自已导入的资料。</p></div>{selected&&<span className="brand-version">v{selected.version}</span>}</div>
      <form onSubmit={save}><fieldset disabled={!canWrite||busy||!ready} className="brand-fields">
        <div className="form-group"><label htmlFor="brand-name">品牌 / 客户名称</label><input id="brand-name" required maxLength={120} value={form.name} onChange={e=>setForm({...form,name:e.target.value})} placeholder="例如：星舟工业设备"/></div>
        <div className="form-group"><label htmlFor="brand-audience">内容主要写给谁</label><textarea id="brand-audience" maxLength={1000} rows={2} value={form.audience} onChange={e=>setForm({...form,audience:e.target.value})} placeholder="例如：需要了解设备选型和维护的采购、工程师"/></div>
        <div className="form-group"><label htmlFor="brand-tone">希望怎么表达</label><textarea id="brand-tone" maxLength={1000} rows={2} value={form.tone} onChange={e=>setForm({...form,tone:e.target.value})}/></div>
        <div className="form-group"><label htmlFor="brand-kb">这个品牌使用的资料库</label><select id="brand-kb" required value={form.knowledge_base_id||''} onChange={e=>setForm({...form,knowledge_base_id:Number(e.target.value)})}><option value="" disabled>选择资料库</option>{libraries.map(k=><option key={k.id} value={k.id}>{k.name}</option>)}</select><button className="text-action" type="button" onClick={()=>onNavigate(brandKnowledgeRoute(selected,form.knowledge_base_id))}>管理资料库和导入资料 ↗</button></div>
        <details className="calm-details" open={Boolean(selected)}><summary>内容边界与行动引导</summary>
          <div className="form-group"><label htmlFor="brand-prohibited">禁止出现在交付内容中的表述 · 每行一项</label><textarea id="brand-prohibited" maxLength={2000} rows={3} value={form.prohibited_claims} onChange={e=>setForm({...form,prohibited_claims:e.target.value})} placeholder={'例如：\n百分百有效\n行业第一'}/><p className="subtle">系统在审核时检查原文是否包含这些表述；含义相近的说法仍需人工检查。</p></div>
          <div className="form-group"><label htmlFor="brand-cta">希望读者下一步做什么</label><input id="brand-cta" maxLength={1000} value={form.call_to_action} onChange={e=>setForm({...form,call_to_action:e.target.value})} placeholder="例如：查看产品使用手册，不要求添加私人联系方式"/></div>
          <div className="form-group"><label htmlFor="brand-policy">品牌任务的生成方式</label><select id="brand-policy" value={form.data_policy} onChange={e=>setForm({...form,data_policy:e.target.value as BrandInput['data_policy']})}><option value="cloud_allowed">允许使用已配置的在线模型</option><option value="local_only">品牌任务仅使用本地摘录</option></select><p className="subtle">此设置约束使用该品牌新建的内容任务。在线模型会收到任务要求及检索片段；本地摘录不是本地大模型。</p></div>
          {selected&&<label className="inline-choice"><input type="checkbox" checked={!form.is_active} onChange={e=>setForm({...form,is_active:!e.target.checked})}/>归档此品牌（保留旧任务，新任务不可选）</label>}
        </details>
        <div className="form-actions"><button className="btn btn-primary" type="submit" disabled={!form.name.trim()||!form.knowledge_base_id}>{busy?'正在保存…':'保存品牌档案'}</button>{selected?.is_active&&<button className="btn" type="button" onClick={()=>onNavigate(`agent?brand=${selected.id}`)}>用已保存档案创建内容 →</button>}</div>
      </fieldset></form>{!canWrite&&<p className="notice-strip">当前角色可以查看品牌档案，修改需要内容编辑权限。</p>}
    </section></div>
  </div>;
}
