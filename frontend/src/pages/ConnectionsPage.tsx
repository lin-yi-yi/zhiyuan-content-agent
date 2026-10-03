import { useEffect, useState } from 'react';
import { Connection, Connections, saasApi } from '../api/saas';
import { useWorkspace } from '../components/WorkspaceContext';
import { request } from '../api/client';
import { sourceHub } from '../api/sourceHub';
import '../styles/connections-simple.css';

const STATUS:Record<string,string>={enabled:'已启用',configured:'已配置',disabled:'已关闭',not_configured:'待配置',authorization_required:'待授权',operator_disabled:'尚未开放',planned:'暂未提供'};
const NAMES:Record<string,string>={local:'本地资料整理',deepseek:'DeepSeek',qwen:'通义千问',doubao:'豆包',kimi:'Kimi'};
const NOTES:Record<string,string>={local:'用规则摘录已导入的资料，不调用在线生成模型。',deepseek:'用于知识问答、内容生成和修改。',qwen:'用于知识问答、内容生成和修改。',doubao:'用于知识问答、内容生成和修改。',kimi:'用于知识问答、内容生成和修改。',aihot:'发现 AI 资讯，查看原始出处。',github:'查看开源项目维护者发布的更新。',markdown:'导入你选择的文档，确认后进入知识库。',obsidian:'手动导入 Markdown 笔记。'};

export default function ConnectionsPage({onNavigate}:{onNavigate:(page:string)=>void}) {
  const {isOwner,session}=useWorkspace();
  const [data,setData]=useState<Connections|null>(null);
  const [error,setError]=useState('');const [message,setMessage]=useState('');
  const [busy,setBusy]=useState('');const [action,setAction]=useState<'test'|'save'|null>(null);
  const [tested,setTested]=useState<Record<string,boolean>>({});
  const local=session.mode==='local';
  const load=()=>{
    setError('');
    const loadData=local?Promise.all([
      request<{providers:Array<{provider:string;model:string;configured:boolean}>}>('/api/models/providers'),sourceHub.status(),sourceHub.githubStatus(),
    ]).then(([models,aihot,github]):Connections=>({default_model_provider:'local',connections:[
      ...models.providers.map(item=>({provider:item.provider,name:NAMES[item.provider]||item.provider,kind:'model' as const,supported:true,enabled:item.configured,effective_enabled:item.configured,configured:item.configured,status:item.provider==='local'?'enabled':item.configured?'configured':'not_configured',can_toggle:false,is_default:false,credential_status:item.provider==='local'?'not_required':item.configured?'configured':'not_configured',credential_source:item.provider==='local'?null:'operator_environment',description:item.model||'使用本地环境中的配置'})),
      ...[{...aihot,provider:'aihot'},{...github,provider:'github'}].map(item=>({provider:item.provider,name:item.name,kind:'source' as const,supported:true,enabled:item.enabled,effective_enabled:item.enabled,configured:item.enabled,status:item.enabled?'enabled':'disabled',can_toggle:false,is_default:false,credential_status:'not_required',credential_source:null,description:'读取本地配置中的信源状态；是否正常返回内容，请在发现信源中查看。'})),
    ]})):saasApi.connections();
    loadData.then(setData).catch(cause=>setError(cause instanceof Error?cause.message:'暂时无法读取连接。'));
  };
  useEffect(load,[]);
  const change=async(item:Connection,body:{enabled?:boolean;is_default?:boolean})=>{
    setBusy(item.provider);setAction('save');setError('');setMessage('');
    try{setData(await saasApi.updateConnection(item.provider,body));setMessage(`${item.name}的设置已更新。`);}
    catch(cause){setError(cause instanceof Error?cause.message:'设置未保存');}
    finally{setBusy('');setAction(null);}
  };
  const test=async(item:Connection)=>{
    setBusy(item.provider);setAction('test');setError('');setMessage('');
    try{
      const result=await saasApi.testConnection(item.provider);
      if(!result.ok)throw new Error('连接测试未通过，请检查服务配置。');
      setTested(previous=>({...previous,[item.provider]:true}));
      setMessage(`${item.name}刚刚连接成功。${local?'可以在提问或创作时选择它。':'本次测试计入 1 次 AI 请求用量。'}`);
    }catch(cause){setTested(previous=>({...previous,[item.provider]:false}));setError(cause instanceof Error?cause.message:'连接测试失败');}
    finally{setBusy('');setAction(null);}
  };
  const renderCard=(item:Connection)=><article className="connection-card" key={item.provider}>
    <div className="section-heading"><span className={`connection-icon ${item.kind}`}>{(NAMES[item.provider]||item.name).slice(0,2)}</span><span className={`connection-status ${item.effective_enabled?'ready':''}`}>{STATUS[item.status]||'待确认'}</span></div>
    <h3>{NAMES[item.provider]||item.name}{item.is_default&&<span className="pill">默认模型</span>}</h3><p>{NOTES[item.provider]||item.description}</p>
    {tested[item.provider]!==undefined&&<small className={tested[item.provider]?'connection-test-ok':'connection-test-failed'} role="status">{tested[item.provider]?'本次连接测试成功':'本次连接测试失败'}</small>}
    <div className="form-actions">
      {item.kind==='model'&&item.provider!=='local'&&<button className="btn btn-sm" disabled={!isOwner||!item.effective_enabled||!!busy} onClick={()=>void test(item)}>{busy===item.provider&&action==='test'?'测试中…':local?'测试连接':'测试连接 · 1 次请求'}</button>}
      {!local&&<button className="btn btn-sm" disabled={!isOwner||!item.can_toggle||!!busy} onClick={()=>void change(item,{enabled:!item.enabled})}>{busy===item.provider&&action==='save'?'保存中…':item.enabled?'关闭':'启用'}</button>}
      {!local&&item.kind==='model'&&item.effective_enabled&&!item.is_default&&<button className="btn btn-sm" disabled={!isOwner||!!busy} onClick={()=>void change(item,{is_default:true})}>设为默认</button>}
      {item.kind==='source'&&item.effective_enabled&&<button className="btn btn-sm" onClick={()=>onNavigate('source-hub')}>查看内容</button>}
      {item.kind==='import'&&item.effective_enabled&&<button className="btn btn-sm" onClick={()=>onNavigate('knowledge')}>导入资料</button>}
    </div>
    <details className="connection-card-detail"><summary>连接说明</summary><p>{item.description}</p>
      {item.kind==='model'&&item.provider!=='local'&&<p>{item.credential_status==='configured'?'已配置访问凭据；是否可用以连接测试结果为准。':'尚未配置访问凭据。'} 密钥不会显示在这里。</p>}
      {item.provider==='aihot'&&<p>商业使用需取得来源方授权，启用开关不能代替授权。</p>}
      {!isOwner&&<p>只有工作空间所有者可以修改连接或执行连接测试。</p>}
    </details>
  </article>;
  const models=data?.connections.filter(item=>item.kind==='model')||[];
  const readyModels=models.filter(item=>item.effective_enabled),otherModels=models.filter(item=>!item.effective_enabled);
  const groups=[['source','内容信源'],['import','资料导入']] as const;
  const integrations=data?.connections.filter(item=>item.kind==='integration')||[];
  return <div className="connections-simple">
    <div className="page-header"><h1>模型与信源</h1><p>管理内容生成服务与资料来源，供创作任务选择使用。</p></div>
    {local&&<p className="connection-mode-note">当前使用本地配置。可以测试已配置的服务，创建内容任务时再选择模型。使用在线模型时，相关资料会发送给对应服务商。</p>}
    {!local&&!isOwner&&<div className="notice-strip">你可以查看连接状态。修改设置和测试连接由组织所有者操作。</div>}
    {error&&<div className="feedback error" role="alert">{error}<button className="btn btn-sm" disabled={!!busy} onClick={load}>重新读取</button></div>}
    {message&&<div className="feedback success" role="status">{message}</div>}
    {!data&&!error&&<div className="panel saas-loading">正在读取连接状态…</div>}
    {data&&<>
      <section className="connection-group"><div className="section-heading"><h2>可用模型</h2><button className="btn btn-text" onClick={()=>onNavigate('settings')}>模型设置</button></div><div className="connection-grid">{readyModels.map(renderCard)}</div>
        {!!otherModels.length&&<details className="connection-more"><summary>其他模型 · {otherModels.length} 项</summary><div className="connection-grid">{otherModels.map(renderCard)}</div></details>}
      </section>
      {groups.map(([kind,label])=>{const items=data.connections.filter(item=>item.kind===kind);return items.length?<section className="connection-group" key={kind}><div className="section-heading"><h2>{label}</h2></div><div className="connection-grid">{items.map(renderCard)}</div></section>:null;})}
      {!!integrations.length&&<details className="connection-more"><summary>协作应用 · {integrations.length} 项</summary><div className="connection-grid">{integrations.map(renderCard)}</div></details>}
    </>}
    <section className="notice-strip" aria-labelledby="platform-delivery-title">
      <div className="section-heading" style={{marginBottom:8}}><strong id="platform-delivery-title">平台交付</strong><button className="text-action" onClick={()=>onNavigate('drafts')}>审核与导出 →</button></div>
      <p>当前可导出图文卡片 PNG / ZIP，并下载任务的审核交付清单。</p>
      <p className="subtle" style={{marginBottom:0}}>微信公众号、小红书、抖音尚未连接发布账号，不支持自动发布。导出后由你在各平台官方后台手动上传；平台专属排版与频道适配尚未提供。</p>
    </section>
    <details className="connection-advanced-tools panel"><summary>高级工具</summary><p>日常从内容任务开始；单独整理素材、维护备选选题或检查连接问题时使用以下工具。</p><div className="connection-tool-grid">
      <button className="connection-tool" onClick={()=>onNavigate('sources')}><strong>素材库 →</strong><span>查看采集与导入的原始素材</span></button>
      <button className="connection-tool" onClick={()=>onNavigate('topics')}><strong>选题池 →</strong><span>整理备选选题并单独生成稿件</span></button>
      <button className="connection-tool" onClick={()=>onNavigate('settings')}><strong>运行诊断 →</strong><span>检查服务配置、连接和调用记录</span></button>
    </div></details>
  </div>;
}
