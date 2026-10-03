import { request, AgentRunResult } from './client';

export interface KnowledgeDocument {
  id: number; title: string; source_uri: string; status: string; chunk_count: number;
  workspace_id: number; knowledge_base_id: number;
  content_hash: string; created_at: string; updated_at: string; metadata: Record<string, unknown>;
  content?: string;
}
export interface RetrievalStatus {
  mode: string; embedding_provider: string; embedding_model: string; vector_store: string;
  semantic_enabled: boolean; configured: boolean; limitations: string[]; document_count: number; chunk_count: number;
}
export interface EvaluationCase {question: string; expected_document_ids?: number[]; expected_chunk_ids?: number[]; should_refuse?: boolean}
export interface Job {
  id: string; company: string; title: string; location: string; education: string; experience: string;
  source_url: string; query_date: string; evidence_grade: string; detail_body_read: boolean;
  full_jd?: boolean; updated_at?: string|null; recruitment_status?: string;
  responsibilities_summary: string | string[]; skill_keywords: string[];
}
export interface Portfolio {
  version: string; scope: string; counts: Record<string, number>;
  providers: Array<{provider: string; model: string; configured: boolean}>;
  jobs: Job[]; jobs_note: string; latest_evaluation: Record<string, unknown> | null;
}
export const workbench = {
  status: () => request<RetrievalStatus>('/api/knowledge/status'),
  documents: () => request<{items: KnowledgeDocument[]; total: number}>('/api/knowledge/documents'),
  document: (id: number) => request<KnowledgeDocument>(`/api/knowledge/documents/${id}`),
  saveDocument: (body: {title: string; content: string; source_uri?: string; document_id?: number}) => request<{document: KnowledgeDocument; deduplicated: boolean}>('/api/knowledge/documents', {method: 'POST', body: JSON.stringify(body)}),
  deleteDocument: (id: number) => request(`/api/knowledge/documents/${id}`, {method: 'DELETE'}),
  reindex: (id: number) => request(`/api/knowledge/documents/${id}/reindex`, {method: 'POST'}),
  evaluate: (cases: EvaluationCase[]) => request<Record<string, unknown>>('/api/knowledge/evaluate', {method:'POST', body: JSON.stringify({cases, top_k:3, dataset_label:'人工编写的 12 题开发演示集；不是独立测试集'})}),
  portfolio: () => request<Portfolio>('/api/portfolio'),
  loadDemo: () => request<{documents: number}>('/api/portfolio/demo', {method:'POST'}),
  demoCases: () => request<{cases: EvaluationCase[]}>('/api/portfolio/evaluation-cases'),
  review: (id: number, decision: 'approve'|'reject', note: string) => request<AgentRunResult>(`/api/agent-runs/${id}/review`, {method:'POST', body:JSON.stringify({decision,note})}),
  cancel: (id: number) => request<AgentRunResult>(`/api/agent-runs/${id}/cancel`, {method:'POST'}),
  submitReview: (id: number) => request<AgentRunResult>(`/api/agent-runs/${id}/submit-review`, {method:'POST'}),
};
