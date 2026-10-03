import { request } from './client';

export type EvidenceProvider = 'manual' | 'github' | 'aihot';
export type EvidenceReviewStatus = 'pending' | 'verified' | 'rejected' | 'revoked';
export type EvidenceRights = 'own' | 'permission' | 'reference_only';
export interface EvidenceScope { workspace_id: number; knowledge_base_id: number }
export interface EvidenceSeed { title: string; source_url: string; provider: EvidenceProvider; version_label?:string }
export interface EvidenceCitation {
  claim: string; excerpt: string; source_url: string; locator?: string;
  product_model?: string; parameter?: string; value?: string;
}
export interface EvidenceCreate extends EvidenceSeed {
  content: string;
  version_label: string;
  published_at?: string | null;
  expires_at: string | null;
  citations: EvidenceCitation[];
  rights_basis: EvidenceRights;
  rights_note: string;
  own_note_confirmed: boolean;
}
export interface EvidenceNote extends EvidenceCreate, EvidenceScope {
  id: number;
  review_status: EvidenceReviewStatus;
  index_status: 'not_indexed' | 'indexed' | 'stale';
  document_id: number | null;
  is_expired: boolean;
  fact_conflict_note_ids?: number[];
  reviewed_at?: string | null;
  verified_at?: string | null;
  review_note?: string | null;
  created_at: string;
  updated_at: string;
}
const scopeQuery = (scope?: EvidenceScope) => scope ? `?${new URLSearchParams({workspace_id:String(scope.workspace_id),knowledge_base_id:String(scope.knowledge_base_id)})}` : '';
export const evidenceApi = {
  list: (scope?: EvidenceScope) => request<{items: EvidenceNote[]; total: number}>(`/api/evidence/notes${scopeQuery(scope)}`),
  get:(id:number, scope?: EvidenceScope)=>request<EvidenceNote>(`/api/evidence/notes/${id}${scopeQuery(scope)}`),
  create: (body: EvidenceCreate, scope?: EvidenceScope) => request<EvidenceNote>('/api/evidence/notes', {method:'POST', body:JSON.stringify({...body,...scope})}),
  review: (id: number, decision: 'verify'|'reject'|'revoke', confirmed_sources: boolean, note: string, scope?: EvidenceScope) =>
    request<EvidenceNote>(`/api/evidence/notes/${id}/review`, {method:'POST', body:JSON.stringify({decision, confirmed_sources, note,...scope})}),
  index: (id: number, scope?: EvidenceScope) => request<{note: EvidenceNote; document_id: number; deduplicated: boolean}>(`/api/evidence/notes/${id}/index`, {method:'POST', body:JSON.stringify(scope||{})}),
};

export function safeEvidenceUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; }
  catch { return null; }
}
export function evidenceTime(value: unknown): string {
  if (typeof value !== 'string' || !value) return '未登记';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '未登记' : date.toLocaleString('zh-CN', {hour12:false});
}
export const REVIEW_LABELS: Record<string,string> = {pending:'待人工核验',verified:'人工已核验',rejected:'未通过核验',revoked:'已撤销核验'};
