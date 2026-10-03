import { EvidenceScope, REVIEW_LABELS, evidenceTime, safeEvidenceUrl } from '../api/evidence';

export default function EvidenceProvenance({metadata,scope,onOpenEvidence}:{metadata:Record<string,unknown>;scope?:EvidenceScope;onOpenEvidence?:(id:number,scope?:EvidenceScope)=>void}) {
  const evidence=(metadata.evidence&&typeof metadata.evidence==='object'?metadata.evidence:{}) as Record<string,unknown>;
  const freshness=(metadata.freshness&&typeof metadata.freshness==='object'?metadata.freshness:{}) as Record<string,unknown>;
  const noteId=typeof evidence.note_id==='number'?evidence.note_id:null;
  const status=typeof evidence.status==='string'?evidence.status:'';
  const expired=freshness.status==='expired'||(typeof evidence.expires_at==='string'&&Date.parse(evidence.expires_at)<Date.now());
  return <div className="evidence-provenance">
    <span className={`evidence-badge ${status||'untracked'}`}>{noteId?REVIEW_LABELS[status]||'核验状态未知':'未登记人工核验'}</span>
    {expired&&<span className="evidence-badge revoked">已过期，需复核</span>}{freshness.status==='expires_soon'&&<span className="evidence-badge pending">即将到期</span>}
    {Array.isArray(evidence.fact_conflict_note_ids)&&evidence.fact_conflict_note_ids.length>0&&<span className="evidence-badge revoked">结构化事实冲突，不可作为有效证据</span>}
    <small>版本：{typeof evidence.version_label==='string'&&evidence.version_label?evidence.version_label:'未登记'} · 到期：{evidenceTime(evidence.expires_at||freshness.expires_at)}</small>
    {noteId&&onOpenEvidence&&<button className="btn btn-sm" onClick={()=>onOpenEvidence(noteId,scope)}>查看核验笔记 #{noteId}</button>}
    {safeEvidenceUrl(evidence.source_url)&&<a href={safeEvidenceUrl(evidence.source_url)!} target="_blank" rel="noopener noreferrer">打开笔记登记来源 ↗</a>}
    {!noteId&&<small>历史／直接导入资料，不能据此视为人工已核验。</small>}
  </div>;
}
