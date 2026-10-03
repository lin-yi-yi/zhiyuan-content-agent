import { useEffect, useRef, useState } from 'react';
import { api, KnowledgeBase, RagAnswerResponse, RagSearchHit } from '../api/client';
import { workbench, RetrievalStatus } from '../api/workbench';
import EvidenceProvenance from '../components/EvidenceProvenance';
import { EvidenceScope, safeEvidenceUrl } from '../api/evidence';
import { useWorkspace } from '../components/WorkspaceContext';
import { useAvailableModels } from '../components/useAvailableModels';

type AnswerWithHealth=RagAnswerResponse&{answer_mode?:string;answerability?:string;evidence_health?:{excluded_count:number;expired_count:number;unverified_count:number;untracked_count:number;warnings:string[]}};
const REFUSAL_LABELS:Record<string,string>={missing_structured_facts:'缺少指定产品参数的核验依据',insufficient_evidence:'可用证据不足',evidence_changed:'证据已变更，请重新检索',invalid_or_insufficient_citations:'引用不足或未通过检查'};
const COVERAGE_LABELS:Record<string,string>={sufficient:'证据充足',insufficient:'证据不足'};
const STRATEGY_LABELS:Record<string,string>={semantic_cosine:'语义检索 · 余弦相似度',lexical_overlap:'词项检索 · 词项覆盖',hybrid_bm25_rrf:'双路融合 · BM25 + 向量 + RRF',semantic:'语义检索',lexical:'词项检索',hybrid:'双路融合'};
const asRecord=(value:unknown):Record<string,unknown>=>value&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{};
const formatSignal=(value:unknown)=>typeof value==='number'&&Number.isFinite(value)?value.toFixed(4):'未命中';
const formatRank=(value:unknown)=>typeof value==='number'&&Number.isFinite(value)?`第 ${value} 位`:'未命中';

function RetrievalExplanation({hit}:{hit:RagSearchHit}) {
  const scores=asRecord(hit.metadata?.scores),gate=asRecord(hit.metadata?.evidence_gate);
  const hybrid=scores.strategy==='hybrid_bm25_rrf'||hit.metadata?.retrieval_mode==='hybrid';
  const threshold=hit.metadata?.evidence_threshold;
  const eligible=hybrid?typeof gate.eligible==='boolean'?gate.eligible:null:typeof threshold==='number'?hit.score>=threshold:null;
  return <details className="calm-details"><summary>为什么找到这条资料</summary>
    {hybrid?<><dl style={{display:'grid',gridTemplateColumns:'auto 1fr',gap:'8px 18px',fontSize:12}}>
      <dt>语义召回</dt><dd>{formatRank(scores.semantic_rank)} · 余弦分 {formatSignal(scores.semantic_score)}</dd>
      <dt>BM25 召回</dt><dd>{formatRank(scores.bm25_rank)} · BM25 分 {formatSignal(scores.bm25_score)}</dd>
      <dt>词项覆盖信号</dt><dd>{formatSignal(scores.term_overlap)}（含完整短语加分）</dd>
      <dt>RRF 融合分</dt><dd>{formatSignal(scores.rrf_score)} · 按两路排名合并</dd>
    </dl><p className="subtle">RRF 只决定排序，不用于判定能否引用；BM25 分、余弦分不能直接相加或比较大小。</p>
      <p className="subtle">采用门槛：余弦分 ≥ {formatSignal(gate.semantic_min_score)}，或词项覆盖信号 ≥ {formatSignal(gate.lexical_min_overlap)}。</p>
    </>:<p className="subtle">{STRATEGY_LABELS[String(scores.strategy||'') ]||'检索相关性信号'}：{formatSignal(hit.score)}。{typeof threshold==='number'?`采用门槛为 ${formatSignal(threshold)}。`:'本结果未返回采用门槛。'}</p>}
    <p className="subtle"><strong>{eligible===true?'达到引用采用门槛':eligible===false?'未达到引用采用门槛':'引用采用状态未返回'}</strong>。这是检索相关性的启发式判断，不代表事实已核验，也不保证片段能够支持全部结论。</p>
  </details>;
}

export default function RagLabPage({onOpenEvidence,initialScope,initialKnowledgeBaseId,onNavigate}:{initialScope?:EvidenceScope;initialKnowledgeBaseId?:number;onNavigate?:(page:string)=>void;onOpenEvidence?:(id:number,scope?:EvidenceScope)=>void}) {
  const {canWrite}=useWorkspace();
  const {models,modelsError}=useAvailableModels();
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState<number | null>(null);
  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState(5);
  const [retrievalMode,setRetrievalMode]=useState<''|'semantic'|'lexical'|'hybrid'>('');
  const [provider, setProvider] = useState('local');
  const [hits, setHits] = useState<RagSearchHit[]>([]);
  const [answer, setAnswer] = useState<AnswerWithHealth | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [resultContext,setResultContext]=useState<{query:string;knowledgeBase:string;strategy:string}|null>(null);
  const retrievalGeneration=useRef(0);
  const [status,setStatus]=useState<RetrievalStatus|null>(null);
  const [statusLoading,setStatusLoading]=useState(true);
  const [report,setReport]=useState<Record<string,unknown>|null>(null);
  const metricValues=(report?.metrics||{}) as Record<string,number|null>;
  const reportCases=(report?.cases||[]) as Array<{question:string;refused:boolean;should_refuse:boolean;recall_at_k:number|null;latency_ms:number}>;
  const runEvaluation=async()=>{setLoading(true);setError('');try{const sample=await workbench.demoCases();setReport(await workbench.evaluate(sample.cases));}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setLoading(false);}};
  const downloadReport=()=>{if(!report)return;const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='rag-evaluation.json';link.click();URL.revokeObjectURL(url);};

  useEffect(() => {
    workbench.status().then(setStatus).catch(e=>setError(String(e))).finally(()=>setStatusLoading(false));
    api.listKnowledgeBases(initialScope?.workspace_id).then(items => {
      setKnowledgeBases(items);
      const requested=initialKnowledgeBaseId??initialScope?.knowledge_base_id;
      if(requested&&!items.some(item=>item.id===requested)){setKnowledgeBaseId(null);setError('指定知识库不可用，请重新选择。');}
      else setKnowledgeBaseId(requested??items[0]?.id??null);
    }).catch(() => {setKnowledgeBases([]);setError('知识库读取失败，请刷新重试。');});
  }, []);

  useEffect(()=>{if(models.length)setProvider((models.find(item=>item.is_default)||models.find(item=>item.provider==='local')||models[0]).provider);},[models]);
  useEffect(()=>{retrievalGeneration.current+=1;setHits([]);setAnswer(null);setResultContext(null);setError('');},[query,retrievalMode,knowledgeBaseId,topK,provider]);
  const handleSearch = async () => {
    if (!query.trim()) return;
    const generation=++retrievalGeneration.current;
    const searchedQuery=query.trim(),searchedKnowledgeBase=knowledgeBases.find(item=>item.id===knowledgeBaseId)?.name||'当前知识库';
    setLoading(true);
    setError('');
    setAnswer(null);
    setHits([]);
    setResultContext(null);
    try {
      const result = await api.searchRag({
        query: query.trim(),
        workspace_id: initialScope?.workspace_id,
        knowledge_base_id: knowledgeBaseId,
        top_k: topK,
        retrieval_mode:retrievalMode||undefined,
      });
      if(generation!==retrievalGeneration.current)return;
      setHits(result.items);
      setResultContext({query:searchedQuery,knowledgeBase:searchedKnowledgeBase,strategy:result.strategy||result.retrieval?.strategy||result.retrieval?.mode||'unknown'});
    } catch (err) {
      if(generation===retrievalGeneration.current)setError(err instanceof Error ? err.message : String(err));
    } finally {setLoading(false);}
  };

  const handleAnswer = async () => {
    if (!query.trim()) return;
    const generation=++retrievalGeneration.current;
    const searchedQuery=query.trim(),searchedKnowledgeBase=knowledgeBases.find(item=>item.id===knowledgeBaseId)?.name||'当前知识库';
    setLoading(true);
    setError('');
    setAnswer(null);
    setHits([]);
    setResultContext(null);
    try {
      const result = await api.answerWithRag({
        query: query.trim(),
        workspace_id: initialScope?.workspace_id,
        knowledge_base_id: knowledgeBaseId,
        provider,
        top_k: topK,
        retrieval_mode:retrievalMode||undefined,
      });
      if(generation!==retrievalGeneration.current)return;
      setAnswer(result);
      setHits(result.citations || []);
      setResultContext({query:searchedQuery,knowledgeBase:searchedKnowledgeBase,strategy:result.retrieval?.strategy||result.retrieval?.mode||'unknown'});
    } catch (err) {
      if(generation===retrievalGeneration.current)setError(err instanceof Error ? err.message : String(err));
    } finally {setLoading(false);}
  };

  return (
    <div>
      <div className="page-header compact-page-heading"><div><h1>问你的知识库。</h1><p>回答带着出处，资料不足时会明确告诉你。</p></div></div>
      <p className="subtle" style={{marginBottom:20}}>{status?`当前默认：${status.mode==='hybrid'?'双路融合检索':status.mode==='semantic'?'中文语义检索':'词项检索'}`:statusLoading?'正在读取检索配置…':'尚未取得检索配置'} · 模型生成的回答仍需要核对。</p>

      {modelsError&&<div className="feedback error">{modelsError}</div>}
      <section className="rag-lab-layout">
        <div className="panel">
          <h3>查询</h3>
          <div className="form-group">
            <label htmlFor="rag-kb">知识库</label>
            <select id="rag-kb" disabled={loading} value={knowledgeBaseId || ''} onChange={e => {setKnowledgeBaseId(e.target.value ? Number(e.target.value) : null);setAnswer(null);setHits([]);}}>
              <option value="" disabled>选择知识库</option>
              {knowledgeBases.map(item => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label htmlFor="rag-question">你想了解什么？</label>
            <textarea id="rag-question" disabled={loading} placeholder="例如：资料中规定的审核步骤是什么？" rows={4} value={query} onChange={e => setQuery(e.target.value)} />
          </div>
          <div className="form-group"><label htmlFor="rag-model">回答方式</label><select id="rag-model" disabled={loading} value={provider} onChange={e=>setProvider(e.target.value)}>{models.map(item=><option key={item.provider} value={item.provider}>{item.provider==='local'?'原文摘录 · 无需密钥':item.model||item.provider}</option>)}</select><p className="task-model-hint">{provider==='local'?'摘录相关原文，保留来源。':'将问题和检索到的片段发送给所选模型。'}</p></div>
          <details className="calm-details"><summary>检索设置与实验</summary><div className="form-group"><label htmlFor="rag-strategy">检索策略</label><select id="rag-strategy" disabled={loading} value={retrievalMode} onChange={e=>setRetrievalMode(e.target.value as typeof retrievalMode)}><option value="">跟随系统默认</option><option value="semantic">语义检索 · 理解相近含义</option><option value="hybrid">双路融合 · BM25 + 向量 + RRF</option><option value="lexical">词项检索 · 无需向量</option></select><p className="subtle">双路融合适合比较术语和语义召回；需要已有语义索引，结果不保证优于单路。排序分不代表答案正确率。</p></div><div className="form-group"><label htmlFor="rag-topk">最多展示的证据</label><select id="rag-topk" disabled={loading} value={topK} onChange={e=>setTopK(Number(e.target.value))}><option value={3}>3 条</option><option value={5}>5 条</option><option value={8}>8 条</option></select></div><button className="btn" onClick={handleSearch} disabled={loading||!query.trim()||!knowledgeBaseId}>只查看检索结果</button></details>
          <div className="form-actions">

            <button className="btn btn-primary" onClick={handleAnswer} disabled={loading || !query.trim() || !models.length || !knowledgeBaseId}>{loading?'正在查找依据…':'提问 →'}</button>
          </div>
          {error && <div className="source-rag-status error">{error}</div>}
        </div>

        <div className="panel">
          <h3>回答</h3>
          {!answer ? (
            <div className="empty" style={{ padding: 24 }}>输入问题后，回答和引用会显示在这里。</div>
          ) : (
            <div className={`rag-answer-box ${answer.refused ? 'refused' : 'ok'}`}>
              <span>{answer.refused ? '已拒答' : answer.answer_mode==='extractive'?'找到相关摘录':'已生成'}</span>
              {!answer.refused&&answer.answer_mode==='extractive'&&<p className="subtle">摘录模式只返回相关原文，未判断你的全部问题是否有答案；没有登记的参数仍需补充确认。</p>}
              <strong>{answer.refused ? REFUSAL_LABELS[answer.refusal_reason||'']||'暂时无法基于现有证据回答' : answer.answer_mode==='extractive'?'原文摘录':'基于证据生成'}</strong>
              <p>{answer.answer}</p>
              <small>
                证据状态：{COVERAGE_LABELS[String(answer.coverage?.status||'')]||'未确定'} ·
                证据 {String(answer.coverage?.evidence_count || 0)} 条 ·
                已附来源，可在下方核对
              </small>
              {answer.evidence_health&&<details className="evidence-health" role="status"><summary>知识库资料状态</summary><p>排除 {answer.evidence_health.excluded_count} 份 · 过期 {answer.evidence_health.expired_count} 份 · 未核验或已撤销 {answer.evidence_health.unverified_count} 份 · 未登记核验 {answer.evidence_health.untracked_count} 份</p><p>这是整个知识库的状态统计，不表示这些资料都与本问题相关。</p>{answer.evidence_health.warnings?.map((warning,index)=><p key={index}>{warning}</p>)}</details>}
            </div>
          )}
        </div>
      </section>

      <section className="panel">
        <h3>回答依据</h3>
        {resultContext&&<p className="subtle" role="status" style={{margin:'10px 0 16px'}}>本次问题：{resultContext.query} · {resultContext.knowledgeBase} · 实际策略：{STRATEGY_LABELS[resultContext.strategy]||'服务端未返回策略'}</p>}
        {hits.length === 0 ? (
          <div className="empty" style={{ padding: 24 }}>暂无检索结果</div>
        ) : (
          <div className="rag-hit-grid">
            {hits.map((hit) => (
              <div key={hit.chunk_id} className="rag-hit-item">
                <div>
                  <span>来源片段 #{hit.chunk_id} · 排序分 {Number(hit.score).toFixed(4)}</span>
                  <strong>{hit.title}</strong>
                </div>
                <p>{hit.content}</p>
                <RetrievalExplanation hit={hit}/>
                <EvidenceProvenance metadata={hit.metadata} scope={{workspace_id:hit.workspace_id,knowledge_base_id:hit.knowledge_base_id}} onOpenEvidence={onOpenEvidence}/>
                {safeEvidenceUrl(hit.source_uri) ? <a href={safeEvidenceUrl(hit.source_uri)!} target="_blank" rel="noopener noreferrer">查看来源</a> : <small>{hit.source_uri}</small>}
              </div>
            ))}
          </div>
        )}
      </section>
      <details className="panel"><summary style={{cursor:'pointer',fontWeight:600,marginBottom:16}}>质量检查</summary><div className="section-heading"><div><h3>固定样本评测</h3><p className="subtle">在默认知识库运行项目自带的合成样本，不受上方知识库选择影响。结果只代表这组演示用例。</p></div><button className="btn btn-primary" disabled={!canWrite||loading} onClick={()=>void runEvaluation()}>{loading?'运行中…':'运行演示评测'}</button></div>
        {report && <><div className="overview-stats">{[['recall_at_k','候选召回率'],['refusal_accuracy','无关问题拒答率'],['answerable_acceptance_rate','可答问题通过率'],['answer_evidence_precision','采用证据准确率']].map(([key,label])=><div key={key}><span>{label}</span><strong>{metricValues[key]==null?'—':`${(Number(metricValues[key])*100).toFixed(1)}%`}</strong></div>)}</div><p className="subtle">以上是固定演示题的实测结果；引用检查仅核对来源记录存在，不保证每一句话都被证据支持。</p><div className="table-wrap"><table><thead><tr><th>测试问题</th><th>预期</th><th>实际</th><th>结果</th></tr></thead><tbody>{reportCases.map((c,i)=><tr key={i}><td>{c.question}</td><td>{c.should_refuse?'应拒答':'应可答'}</td><td>{c.refused?'拒答':'有证据'}</td><td>{c.refused===c.should_refuse?'符合预期':'需分析'}</td></tr>)}</tbody></table></div><div className="form-actions"><button className="btn" onClick={downloadReport}>下载完整评测报告</button></div><details><summary>查看原始指标、候选和引用记录</summary><pre className="json-view evaluation-report">{JSON.stringify(report,null,2)}</pre></details></>}
      </details>
    </div>
  );
}
