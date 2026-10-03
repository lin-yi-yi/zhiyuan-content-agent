import { useEffect, useRef, useState } from 'react';
import { api, KnowledgeBase, request } from '../api/client';
import { KnowledgeDocument, RetrievalStatus } from '../api/workbench';
import EvidenceProvenance from '../components/EvidenceProvenance';
import { EvidenceScope } from '../api/evidence';
import { useWorkspace } from '../components/WorkspaceContext';
import '../styles/knowledge-import.css';

type ImportPreview = {title:string;content:string;format:'markdown'|'text';file_type:string;page_count:number|null;warnings:string[];character_count:number;knowledge_base_id:number};
type Props = {
  onOpenEvidence?:(id:number,scope?:EvidenceScope)=>void;
  onNavigate?:(page:string)=>void;
  onUseKnowledgeBase?:(page:'rag'|'agent',knowledgeBaseId:number)=>void;
  initialKnowledgeBaseId?:number;
};
const MAX_FILE_SIZE = 1024 * 1024;
const MAX_CONTENT = 120000;
const messageOf = (error:unknown) => error instanceof Error ? error.message : String(error);

export default function KnowledgePage({onOpenEvidence,onNavigate,onUseKnowledgeBase,initialKnowledgeBaseId}:Props) {
  const {canWrite}=useWorkspace();
  const [bases,setBases]=useState<KnowledgeBase[]>([]);
  const [baseId,setBaseId]=useState<number|null>(null);
  const [docs,setDocs]=useState<KnowledgeDocument[]>([]);
  const [total,setTotal]=useState(0);
  const [status,setStatus]=useState<RetrievalStatus|null>(null);
  const [loading,setLoading]=useState(true);
  const [mode,setMode]=useState<'file'|'paste'>('file');
  const [title,setTitle]=useState('');
  const [content,setContent]=useState('');
  const [uri,setUri]=useState('');
  const [format,setFormat]=useState<'markdown'|'text'>('markdown');
  const [editing,setEditing]=useState<number|undefined>();
  const [preview,setPreview]=useState(false);
  const [fileLabel,setFileLabel]=useState('');
  const [warnings,setWarnings]=useState<string[]>([]);
  const [busy,setBusy]=useState(false);
  const [phase,setPhase]=useState('');
  const [message,setMessage]=useState('');
  const [saved,setSaved]=useState<{title:string;knowledgeBaseId:number}|null>(null);
  const [error,setError]=useState('');
  const [deleting,setDeleting]=useState<number|null>(null);
  const [baseName,setBaseName]=useState('');
  const [creatingBase,setCreatingBase]=useState(false);
  const [reading,setReading]=useState<KnowledgeDocument|null>(null);
  const listGeneration=useRef(0);
  const formRef=useRef<HTMLElement>(null);
  const activeBase=bases.find(base=>base.id===baseId);
  const query=(id:number)=>`?knowledge_base_id=${id}`;
  const reset=()=>{setTitle('');setContent('');setUri('');setEditing(undefined);setPreview(false);setWarnings([]);setFileLabel('');setFormat('markdown');setReading(null);};
  const refresh=async(id:number)=>{
    const generation=++listGeneration.current;
    setLoading(true);
    try {
      const [list,state]=await Promise.all([
        request<{items:KnowledgeDocument[];total:number}>(`/api/knowledge/documents${query(id)}&limit=200`),
        request<RetrievalStatus>(`/api/knowledge/status${query(id)}`),
      ]);
      if(generation===listGeneration.current){setDocs(list.items);setTotal(list.total);setStatus(state);}
    } finally {if(generation===listGeneration.current)setLoading(false);}
  };
  useEffect(()=>{
    let cancelled=false;
    void (async()=>{
      // Initializes the existing default library for a new workspace.
      await request('/api/knowledge/status');
      const list=await api.listKnowledgeBases();
      if(cancelled)return;
      setBases(list);
      if(initialKnowledgeBaseId && !list.some(base=>base.id===initialKnowledgeBaseId)) {
        setError('这个知识库不属于当前工作区，请选择一个可用知识库。');setBaseId(null);setLoading(false);return;
      }
      setBaseId(initialKnowledgeBaseId??list[0]?.id??null);
      if(!list.length)setLoading(false);
    })().catch(cause=>{if(!cancelled){setError(messageOf(cause));setLoading(false);}});
    return ()=>{cancelled=true;++listGeneration.current;};
  },[initialKnowledgeBaseId]);
  useEffect(()=>{
    if(!baseId)return;
    reset();setSaved(null);setMessage('');setDocs([]);setStatus(null);setDeleting(null);
    void refresh(baseId).catch(cause=>setError(messageOf(cause)));
  },[baseId]);
  const act=async(label:string,fn:()=>Promise<void>)=>{
    setBusy(true);setPhase(label);setError('');setMessage('');
    try{await fn();}catch(cause){setError(messageOf(cause));}finally{setBusy(false);setPhase('');}
  };
  const readFile=async(file?:File)=>{
    if(!file||!baseId)return;
    // A failed replacement must not leave the previous preview confirmable.
    reset();setSaved(null);
    if(!/\.(md|txt|markdown|pdf|docx)$/i.test(file.name)){setError('请选择 Markdown、TXT、PDF 或 DOCX；旧版 .doc 请另存为 .docx。');return;}
    if(!file.size||file.size>MAX_FILE_SIZE){setError('请选择非空且不超过 1 MiB 的文件。');return;}
    await act('正在提取文字…',async()=>{
      const bytes=new Uint8Array(await file.arrayBuffer());
      let binary='';
      for(let index=0;index<bytes.length;index+=8192)binary+=String.fromCharCode(...bytes.subarray(index,index+8192));
      const result=await request<ImportPreview>('/api/knowledge/import-preview',{method:'POST',body:JSON.stringify({filename:file.name,content_base64:btoa(binary),knowledge_base_id:baseId})});
      if(result.knowledge_base_id!==baseId)throw new Error('知识库发生变化，请重新选择文件。');
      setTitle(result.title);setContent(result.content);setFormat(result.format);setWarnings(result.warnings);setFileLabel(`${file.name}${result.page_count?` · ${result.page_count} 页`:''}`);setPreview(true);
    });
  };
  const save=()=>void act('正在建立检索索引…',async()=>{
    if(!baseId)return;
    const result=await request<{document:KnowledgeDocument;deduplicated:boolean}>('/api/knowledge/documents',{
      method:'POST',body:JSON.stringify({title:title.trim(),content:content.trim(),source_uri:uri.trim(),format,document_id:editing,knowledge_base_id:baseId}),
    });
    setSaved({title:result.document.title,knowledgeBaseId:baseId});reset();
    setMessage(result.deduplicated?'这份正文已在当前知识库中，已复用现有资料，没有重复入库。':'资料已入库，可以开始提问或创作。');
    await refresh(baseId);
  });
  const loadDocument=(doc:KnowledgeDocument)=>void act('正在读取正文…',async()=>{
    if(!baseId)return;
    const detail=await request<KnowledgeDocument>(`/api/knowledge/documents/${doc.id}${query(baseId)}`);
    if(!canWrite||Boolean(doc.metadata.evidence)){setReading(detail);return;}
    setTitle(detail.title);setContent(detail.content||'');setUri(detail.source_uri||'');setEditing(detail.id);setPreview(true);setMode('paste');setFileLabel('');setWarnings([]);setSaved(null);setReading(null);
    formRef.current?.scrollIntoView({behavior:'smooth',block:'start'});
  });
  const openDestination=(page:'rag'|'agent',id:number)=>onUseKnowledgeBase?onUseKnowledgeBase(page,id):onNavigate?.(page);
  return <div className="knowledge-simple">
    <div className="page-header"><h1>我的资料</h1><p>放入一份资料，检查正文，然后开始提问或创作。</p></div>
    <div className="knowledge-library-bar">
      <label htmlFor="knowledge-library">当前知识库</label>
      <select id="knowledge-library" value={baseId??''} disabled={busy||!bases.length} onChange={event=>{setError('');setBaseId(Number(event.target.value));}}>
        {!baseId&&<option value="">请选择知识库</option>}{bases.map(base=><option key={base.id} value={base.id}>{base.name}</option>)}
      </select>
      <span className="subtle">{loading?'正在读取…':`${total} 份资料`}</span>
      {canWrite&&<button className="btn btn-text" disabled={busy} onClick={()=>setCreatingBase(value=>!value)}>新建知识库</button>}
    </div>
    {creatingBase&&<form className="knowledge-create-base" onSubmit={event=>{event.preventDefault();void act('正在创建知识库…',async()=>{const base=await request<KnowledgeBase>('/api/v04/knowledge-bases',{method:'POST',body:JSON.stringify({name:baseName.trim()})});setBases(current=>[...current,base]);setBaseName('');setCreatingBase(false);setBaseId(base.id);});}}>
      <label htmlFor="knowledge-new-name">知识库名称</label><input id="knowledge-new-name" value={baseName} maxLength={120} onChange={event=>setBaseName(event.target.value)} placeholder="例如：产品资料、AI 学习笔记" autoFocus/>
      <button className="btn btn-primary" disabled={busy||!baseName.trim()}>创建</button><button className="btn" type="button" onClick={()=>setCreatingBase(false)}>取消</button>
    </form>}
    {error&&<div className="feedback error" role="alert">{error}</div>}
    {message&&<div className="feedback success" role="status">{message}</div>}
    {saved&&<section className="knowledge-next panel"><div><strong>“{saved.title}”已准备好</strong><p>接下来会继续使用这份资料所在的知识库。</p></div><div className="form-actions">
      {(onUseKnowledgeBase||onNavigate)&&<><button className="btn btn-primary" onClick={()=>openDestination('rag',saved.knowledgeBaseId)}>用资料提问</button><button className="btn" onClick={()=>openDestination('agent',saved.knowledgeBaseId)}>用资料创作</button></>}
      <button className="btn btn-text" onClick={()=>{setSaved(null);setMessage('');}}>继续添加</button>
    </div></section>}
    {canWrite?<section className="panel knowledge-import-panel" ref={formRef}>
      <div className="section-heading"><h2>{editing?'修改资料':preview?'确认提取正文':'添加一份资料'}</h2>{(preview||editing)&&<button className="btn btn-text" disabled={busy} onClick={reset}>重新选择</button>}</div>
      <ol className="knowledge-import-steps" aria-label="导入进度"><li className={!preview?'active':'complete'}>1 选择资料</li><li className={preview?'active':''}>2 检查并入库</li></ol>
      <fieldset className="knowledge-import-fields" disabled={busy||!baseId}>
        {!preview&&<>
          <div className="knowledge-input-modes" aria-label="添加方式"><button type="button" className={mode==='file'?'selected':''} aria-pressed={mode==='file'} onClick={()=>{reset();setMode('file');setError('');}}>选择文件</button><button type="button" className={mode==='paste'?'selected':''} aria-pressed={mode==='paste'} onClick={()=>{reset();setMode('paste');setError('');}}>粘贴正文</button></div>
          {mode==='file'?<label className="knowledge-file-picker" htmlFor="knowledge-file"><strong>选择要导入的文件</strong><span>Markdown / TXT / PDF / Word（.docx）</span><small>单文件不超过 1 MiB · PDF 最多 50 页 · 暂不支持扫描件 OCR</small><input id="knowledge-file" type="file" accept=".md,.markdown,.txt,.pdf,.docx" onChange={event=>{void readFile(event.target.files?.[0]);event.target.value='';}}/></label>:<>
            <div className="form-group"><label htmlFor="doc-title">资料标题</label><input id="doc-title" value={title} maxLength={200} onChange={event=>setTitle(event.target.value)} placeholder="例如：产品说明、技术笔记"/></div>
            <div className="form-group"><label htmlFor="doc-content">粘贴正文</label><textarea id="doc-content" rows={8} maxLength={MAX_CONTENT} value={content} onChange={event=>setContent(event.target.value)} placeholder="至少 40 个字符，建议每份资料聚焦一个主题。"/></div>
            <button className="btn btn-primary" disabled={!title.trim()||content.trim().length<40} onClick={()=>{setPreview(true);setFormat('markdown');setSaved(null);setError('');}}>下一步：检查正文</button>
          </>}
        </>}
        {preview&&<>
          {fileLabel&&<p className="knowledge-file-label">已提取：{fileLabel}</p>}
          {warnings.length>0&&<div className="knowledge-import-warning" role="note">{warnings.map(warning=><p key={warning}>{warning}</p>)}</div>}
          <div className="form-group"><label htmlFor="doc-title">资料标题</label><input id="doc-title" value={title} maxLength={200} onChange={event=>setTitle(event.target.value)}/></div>
          <div className="form-group"><label htmlFor="doc-content">核对正文 <span className="subtle">{content.trim().length.toLocaleString()} / 120,000 字符 · 可直接修正</span></label><textarea id="doc-content" rows={10} maxLength={MAX_CONTENT} value={content} onChange={event=>setContent(event.target.value)}/></div>
          <details className="knowledge-advanced"><summary>来源与导入说明（可选）</summary><div className="form-group"><label htmlFor="doc-uri">来源链接</label><input id="doc-uri" value={uri} maxLength={2000} onChange={event=>setUri(event.target.value)} placeholder="https:// 或 obsidian:// 链接"/></div><p>只导入当前正文，不访问这个链接。导入不代表已核验；需要记录授权、版本或时效的资料，请在“核验笔记”中完成确认。</p></details>
          <div className="knowledge-confirm"><div>保存到 <strong>{activeBase?.name??'当前知识库'}</strong><small>正文至少 40 字符；确认后才会参与检索。</small></div><button className="btn btn-primary" disabled={!title.trim()||content.trim().length<40||content.length>MAX_CONTENT} onClick={save}>{editing?'保存修改并更新索引':'确认入库'}</button></div>
        </>}
      </fieldset>
      {busy&&<p className="knowledge-progress" role="status" aria-live="polite">{phase} 请稍候。</p>}
    </section>:<div className="notice-strip">当前角色可以查看资料。添加或修改资料需要编辑权限。</div>}
    <section className="panel knowledge-document-panel"><div className="section-heading"><h2>已有资料</h2><span className="subtle">{total} 份</span></div>
      {!loading&&!docs.length&&<div className="empty">这里还没有资料。导入后，就能根据自己的资料提问。</div>}
      {docs.map(doc=><article className="knowledge-document-row" key={doc.id}><div className="knowledge-document-summary"><div><strong>{doc.title}</strong><p>{doc.chunk_count} 个检索片段{Boolean(doc.metadata.evidence)?' · 含核验记录':''}</p></div><button className="btn" disabled={busy} onClick={()=>loadDocument(doc)}>{canWrite&&!doc.metadata.evidence?'查看 / 修改':'查看正文'}</button></div>
        <details className="knowledge-advanced"><summary>来源与管理</summary><p className="subtle">{doc.source_uri||'用户主动导入'} · {doc.status}</p><EvidenceProvenance metadata={doc.metadata} scope={{workspace_id:doc.workspace_id,knowledge_base_id:doc.knowledge_base_id}} onOpenEvidence={onOpenEvidence}/>
          {Boolean(doc.metadata.evidence)&&<p>修订核验资料请在对应笔记中撤销旧版，再重新核验入库。</p>}
          <div className="form-actions"><button className="btn" disabled={!canWrite||busy||Boolean(doc.metadata.evidence)} onClick={()=>void act('正在重建索引…',async()=>{if(!baseId)return;await request(`/api/knowledge/documents/${doc.id}/reindex${query(baseId)}`,{method:'POST'});await refresh(baseId);setMessage('这份资料的检索索引已更新。');})}>重建索引</button><button className="btn btn-text" disabled={!canWrite||busy} onClick={()=>setDeleting(doc.id)}>移除资料</button></div>
          {deleting===doc.id&&<div className="feedback error">移除后不再参与知识库检索，素材库原文仍保留。<div className="form-actions"><button className="btn" disabled={!canWrite||busy} onClick={()=>void act('正在移除…',async()=>{if(!baseId)return;await request(`/api/knowledge/documents/${doc.id}${query(baseId)}`,{method:'DELETE'});setDeleting(null);if(editing===doc.id)reset();if(reading?.id===doc.id)setReading(null);await refresh(baseId);setMessage('已从当前知识库移除，素材库原文仍保留。');})}>确认移除</button><button className="btn" onClick={()=>setDeleting(null)}>保留</button></div></div>}
        </details>
      </article>)}
      {total>docs.length&&<p className="subtle">目前展示最近 {docs.length} 份资料，共 {total} 份。</p>}
      {reading&&<section className="knowledge-read-preview"><div className="section-heading"><h3>{reading.title}</h3><button className="btn" onClick={()=>setReading(null)}>收起正文</button></div><pre>{reading.content}</pre></section>}
      <details className="knowledge-advanced knowledge-system-detail"><summary>检索配置与技术详情</summary><p>{status?`${status.mode==='semantic'?'语义检索':status.mode==='hybrid'?'混合检索':'词项检索'} · ${status.embedding_model||'无语义模型'} · ${status.chunk_count} 个片段`:'未取得检索配置。'}</p><p>PDF / Word 只提取可读文字，不提取图片文字。Obsidian 支持用户选择 Markdown 导入，尚未持续同步本地笔记库。</p></details>
    </section>
  </div>;
}
