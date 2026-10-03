import { request } from './client';

export type SourceHubWindow = '24h' | '7d';
export type SourceHubMode = 'selected' | 'all';
export type SourceHubCategory = '' | 'ai-models' | 'ai-products' | 'industry' | 'paper' | 'tip';

export interface SourceHubQuery {
  window: SourceHubWindow;
  mode: SourceHubMode;
  q: string;
  category: SourceHubCategory;
}

export interface SourceHubStatus {
  enabled: boolean;
  name: string;
  homepage_url: string;
  mcp_url: string | null;
  terms_url: string;
  embed_supported: false;
  read_only: true;
}

export interface SourceHubItem {
  id: string;
  title: string;
  originalTitle: string | null;
  summary: string | null;
  source: { name: string };
  links: { aihot: string | null; original: string | null };
  publishedAt: string | null;
  discoveredAt: string;
  category: string | null;
  score: number | null;
  selected: boolean;
  reason: string | null;
  attribution?: { name?: string | null; url?: string | null } | null;
  verification_status: 'unverified';
  content_kind: 'ai_summary' | 'maintainer_release';
  version_label?: string | null;
  source_tier?: string;
}

export type GitHubProject='langchain'|'dify'|'ragflow';
export interface GitHubSourceResponse extends Omit<SourceHubResponse,'provider'|'query'> {
  provider:'github';
  query:{project:GitHubProject};
}

export interface SourceHubResponse {
  provider: 'aihot';
  source: Omit<SourceHubStatus, 'read_only'>;
  query: SourceHubQuery & { by: 'timeline' };
  items: SourceHubItem[];
  refresh: {
    status: 'ready' | 'error' | 'disabled';
    last_attempt_at: string | null;
    last_success_at: string | null;
    next_refresh_at: string | null;
    stale: boolean;
    from_cache: boolean;
    error: string | null;
  };
  page: { count: number; hasMore: boolean; nextCursor: string | null };
}

export const sourceHub = {
  githubStatus:(signal?:AbortSignal)=>request<SourceHubStatus>('/api/source-hub/github/status',{signal}),
  githubRead:(project:GitHubProject,signal?:AbortSignal)=>request<GitHubSourceResponse>(`/api/source-hub/github?${new URLSearchParams({project,limit:'20'})}`,{signal}),
  status: (signal?: AbortSignal) => request<SourceHubStatus>('/api/source-hub/aihot/status', { signal }),
  read: (query: SourceHubQuery, signal?: AbortSignal) => {
    const params = new URLSearchParams({ ...query, q: query.q.trim(), limit: '30' });
    return request<SourceHubResponse>(`/api/source-hub/aihot?${params}`, { signal });
  },
};
