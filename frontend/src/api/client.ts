const API_BASE = '';

async function request<T>(url: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    const text = await res.text();
    try {
      const data = JSON.parse(text);
      throw new Error(data.detail || text || `HTTP ${res.status}`);
    } catch (error) {
      if (error instanceof Error && error.message && error.message !== text) {
        throw error;
      }
      throw new Error(text || `HTTP ${res.status}`);
    }
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export interface Topic {
  id: number;
  source_id: number | null;
  title: string;
  url: string | null;
  source_type: string;
  raw_summary: string | null;
  concise_summary: string | null;
  target_audience: string | null;
  content_angle: string | null;
  recommended_platform: string | null;
  score: number;
  score_reason: string | null;
  status: string;
  risk_level: string;
  created_at: string;
  updated_at: string;
}

export interface TopicList {
  total: number;
  items: Topic[];
}

export interface TopicCreate {
  title: string;
  url?: string;
  source_type?: string;
  raw_summary?: string;
}

export interface TopicImportUrl {
  url: string;
  source_type?: string;
  fallback_summary?: string;
  auto_score?: boolean;
}

export interface TopicImportPreview {
  source_type: string;
  title: string;
  url: string;
  raw_content: string;
  summary: string;
  topic_title: string;
  suggestions: TopicSuggestion[];
}

export interface TopicImportConfirm extends TopicImportPreview {
  content_angle?: string;
  target_audience?: string;
  suggestion_reason?: string;
  risk_tip?: string;
  auto_score?: boolean;
}

export interface TopicSuggestion {
  title: string;
  content_angle: string;
  target_audience: string;
  summary: string;
  reason: string;
  risk_tip: string;
}

export interface ResearchReference {
  title: string;
  url: string;
  summary: string;
  source_type: string;
  status: string;
}

export interface CustomTopicIdea {
  title: string;
  content_angle: string;
  target_audience: string;
  summary: string;
  reason: string;
  risk_tip: string;
  recommended_platform: string;
  source_type: string;
  score: number;
  keywords: string[];
  references: ResearchReference[];
  verification_status: string;
  duplicate_hint?: string;
}

export interface CustomTopicIdeasRequest {
  mode: 'research' | 'inspiration';
  research_depth: 'quick' | 'deep';
  theme: string;
  target_audience?: string;
  viewpoint?: string;
  personal_case?: string;
  content_type?: string;
  source_urls?: string[];
  provider?: string;
  model?: string;
}

export interface CustomTopicIdeasResponse {
  mode: string;
  research_depth: string;
  research_status: string;
  keywords: string[];
  references: ResearchReference[];
  ideas: CustomTopicIdea[];
}

export interface CustomTopicConfirmRequest extends CustomTopicIdea {
  auto_score?: boolean;
  provider?: string;
  model?: string;
}

export interface SourceItem {
  id: number;
  source_type: string;
  title: string;
  url: string | null;
  summary: string | null;
  created_at: string | null;
  topic_count: number;
  quality_flags?: string[];
  duplicate_hint?: string;
  rag_index?: SourceRagIndexStatus;
}

export interface SourceRagIndexStatus {
  indexed: boolean;
  workspace_id: number;
  knowledge_base_id: number;
  document_count: number;
  chunk_count: number;
  last_document_id: number | null;
  updated_at: string | null;
}

export interface SourceList {
  total: number;
  page: number;
  items: SourceItem[];
}

export interface SourceStats {
  total_sources: number;
  total_topics_from_sources: number;
  by_type: { source_type: string; count: number }[];
}

export interface SourceDetail extends Omit<SourceItem, 'topic_count'> {
  raw_content: string | null;
  quality_flags?: string[];
  duplicate_hint?: string;
  rag_index?: SourceRagIndexStatus;
  topics: Array<{
    id: number;
    title: string;
    content_angle: string | null;
    score: number;
    status: string;
    created_at: string | null;
  }>;
}

export interface SourceTopicIdea extends CustomTopicIdea {
  duplicate_hint?: string;
}

export interface SourceTopicIdeasRequest {
  target_audience?: string;
  content_type?: string;
  provider?: string;
  model?: string;
  limit?: number;
}

export interface SourceTopicIdeasResponse {
  source_id: number;
  source_title: string;
  research_status: string;
  keywords: string[];
  existing_topics: Array<Record<string, unknown>>;
  ideas: SourceTopicIdea[];
}

export interface SourceTopicIdeaConfirmRequest extends SourceTopicIdea {
  auto_score?: boolean;
  provider?: string;
  model?: string;
}

export interface Draft {
  id: number;
  topic_id: number;
  platform: string;
  title_options: string[] | null;
  cover_text_options: string[] | null;
  body_text: string | null;
  hashtags: string[] | null;
  comment_guide: string | null;
  fact_checks: string[] | null;
  risk_tips: string[] | null;
  aigc_notice: string | null;
  model_provider: string | null;
  model_name: string | null;
  variant_name?: string | null;
  selected_title?: string | null;
  selected_cover_text?: string | null;
  body_variant_key?: string | null;
  body_variants?: Record<string, string> | null;
  content_type?: string | null;
  template_key?: string | null;
  theme_key?: string | null;
  max_card_count?: number | null;
  generated_reason?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface Card {
  id: number;
  draft_id: number;
  page_index: number;
  card_type: string;
  title: string;
  subtitle: string | null;
  body: string | null;
  highlight: string | null;
  footer: string | null;
  layout_key: string;
  theme_key: string;
  style_json?: Record<string, unknown> | null;
  created_at?: string;
  updated_at?: string;
}

export interface PublishLog {
  id: number;
  draft_id: number;
  platform: string;
  published_at: string | null;
  post_url: string | null;
  used_title: string | null;
  used_cover_text: string | null;
  content_type: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface PublishLogCreate {
  draft_id: number;
  platform: string;
  published_at?: string | null;
  post_url?: string;
  used_title?: string;
  used_cover_text?: string;
  content_type?: string;
  notes?: string;
}

export interface ContentPrediction {
  id: number;
  draft_id: number;
  publish_log_id: number | null;
  platform: string;
  predicted_views: number;
  predicted_likes: number;
  predicted_favorites: number;
  predicted_comments: number;
  predicted_shares: number;
  predicted_new_followers: number;
  predicted_save_rate: number | null;
  predicted_like_rate: number | null;
  predicted_comment_rate: number | null;
  predicted_follow_conversion_rate: number | null;
  confidence: number;
  rubric_version: string;
  rationale: string | null;
  risk_notes: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ContentPredictionCreate {
  draft_id: number;
  publish_log_id?: number | null;
  platform?: string;
  predicted_views?: number;
  predicted_likes?: number;
  predicted_favorites?: number;
  predicted_comments?: number;
  predicted_shares?: number;
  predicted_new_followers?: number;
  predicted_save_rate?: number | null;
  predicted_like_rate?: number | null;
  predicted_comment_rate?: number | null;
  predicted_follow_conversion_rate?: number | null;
  confidence?: number;
  rubric_version?: string;
  rationale?: string | null;
  risk_notes?: string | null;
  status?: string;
}

export interface DraftVariantGenerateRequest {
  selected_title?: string;
  selected_cover_text?: string;
  body_variant_key?: string;
  body_variants?: Record<string, string>;
  content_type?: string;
  template_key?: string;
  theme_key?: string;
  max_card_count?: number;
  provider?: string;
  model?: string;
}

export interface DraftVariantMeta {
  variant_name: string;
  selected_title: string;
  selected_cover_text: string;
  body_variant_key: string;
  body_variants: Record<string, string>;
  content_type: string;
  template_key: string;
  theme_key: string;
  max_card_count: number;
  generated_reason: string;
}

export interface DraftVariantResponse {
  draft: Draft;
  cards: Card[];
  variant: DraftVariantMeta;
}

export interface ReviewChecklistItem {
  id: number;
  draft_id: number;
  key: string;
  label: string;
  checked: boolean;
  note: string | null;
}

export interface ReviewChecklistUpdateItem {
  key: string;
  checked: boolean;
  note?: string | null;
}

export interface AgentRunCreate {
  goal: string;
  mode?: 'research' | 'inspiration';
  research_depth?: 'quick' | 'deep';
  target_audience?: string;
  viewpoint?: string;
  personal_case?: string;
  content_type?: string;
  source_urls?: string[];
  provider?: string;
  model?: string;
  auto_score?: boolean;
  use_rag?: boolean;
  workspace_id?: number | null;
  knowledge_base_id?: number | null;
  rag_top_k?: number;
  rag_min_score?: number;
}

export interface AgentStep {
  id: number;
  run_id: number;
  step_index: number;
  key: string;
  label: string;
  status: string;
  input_json: Record<string, unknown> | null;
  output_json: Record<string, unknown> | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentRun {
  id: number;
  goal: string;
  mode: string;
  provider: string;
  model_name: string | null;
  status: string;
  current_step: string | null;
  selected_topic_id: number | null;
  draft_id: number | null;
  evaluation_score: number | null;
  result_json: Record<string, unknown> | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  steps: AgentStep[];
}

export interface AgentRunResult extends AgentRun {
  topic: Topic | null;
  draft: Draft | null;
  cards: Card[];
  evaluation: Record<string, unknown> | null;
}

export interface CapabilitySpec {
  name: string;
  label: string;
  category: string;
  access_level: string;
  allowed_actions: string[];
  data_scope: string;
  can_write: boolean;
  limitations: string[];
}

export interface AgentToolSpec {
  name: string;
  label: string;
  description: string;
  category: string;
  capability: string;
  action: string;
  data_scope: string;
  writes: boolean;
  boundary_rules: string[];
  input_schema: Record<string, unknown>;
}

export interface AgentToolListResponse {
  items: AgentToolSpec[];
  total: number;
}

export interface AgentToolExecuteRequest {
  tool_name: string;
  workspace_id?: number | null;
  knowledge_base_id?: number | null;
  arguments?: Record<string, unknown>;
}

export interface AgentToolExecuteResponse {
  tool_name: string;
  label: string;
  capability: string;
  action: string;
  workspace_id: number;
  knowledge_base_id: number;
  writes: boolean;
  boundary: {
    data_scope: string;
    rules: string[];
  };
  output: Record<string, unknown>;
}

export interface ArchitectureInfo {
  version: string;
  product_boundary: {
    product: string;
    primary_scenario: string;
    in_scope: string[];
    out_of_scope: string[];
  };
  data_isolation: {
    default_workspace_id: number;
    default_knowledge_base_id: number;
    rule: string;
    tables: string[];
    legacy_tables: string;
  };
  retrieval_strategy?: {
    name: string;
    embedding_provider: string;
    embedding_model: string;
    embedding_dim: number;
    scoring: string;
    limitation: string;
  };
  capabilities: CapabilitySpec[];
  tools: AgentToolSpec[];
  framework_status: {
    langchain_available: boolean;
    langgraph_available: boolean;
    langchain_usage: string;
    langgraph_usage: string;
  };
  workflow_boundary: {
    current: string;
    v04_extension: string;
    async_boundary: string;
  };
}

export interface KnowledgeBase {
  id: number;
  workspace_id: number;
  name: string;
  purpose: string | null;
  boundary_notes: string | null;
  status: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface RagIndexSourceRequest {
  source_id: number;
  workspace_id?: number | null;
  knowledge_base_id?: number | null;
  ingestion_profile?: string;
}

export interface RagIndexSourceResult {
  workspace_id: number;
  knowledge_base_id: number;
  document_id: number;
  source_id: number;
  title: string;
  chunk_count: number;
  content_hash: string;
  ingestion_profile: string;
}

export interface RagSearchHit {
  chunk_id: number;
  document_id: number;
  knowledge_base_id: number;
  workspace_id: number;
  source_id: number | null;
  title: string;
  source_uri: string;
  content: string;
  score: number;
  chunk_index: number;
  metadata: Record<string, unknown>;
}

export interface RagSearchResponse {
  items: RagSearchHit[];
  total: number;
}

export interface RagAnswerResponse {
  answer: string;
  refused: boolean;
  refusal_reason: string;
  coverage: Record<string, unknown>;
  citations: RagSearchHit[];
}

export interface Metric {
  id: number;
  publish_log_id: number;
  views: number;
  likes: number;
  favorites: number;
  comments: number;
  shares: number;
  new_followers: number;
  impressions: number | null;
  click_rate: number | null;
  profile_visits: number | null;
  follow_conversion_rate: number | null;
  collected_at: string;
  notes: string | null;
}

export interface MetricCreate {
  views: number;
  likes: number;
  favorites: number;
  comments: number;
  shares: number;
  new_followers: number;
  impressions?: number | null;
  click_rate?: number | null;
  profile_visits?: number | null;
  follow_conversion_rate?: number | null;
  notes?: string;
}

export interface WeeklyReport {
  id: number;
  start_date: string;
  end_date: string;
  report_text: string;
  performance_summary?: {
    totals?: Record<string, unknown>;
    rates?: Record<string, number>;
    prediction_calibration?: Record<string, unknown>;
  } | null;
  best_topics: { items?: Array<Record<string, unknown>> } | null;
  worst_topics: { items?: Array<Record<string, unknown>> } | null;
  angle_performance: { items?: Array<Record<string, unknown>> } | null;
  content_type_performance: { items?: Array<Record<string, unknown>> } | null;
  template_performance: { items?: Array<Record<string, unknown>> } | null;
  prediction_calibration?: Record<string, unknown> | null;
  recommendations: { items?: string[] } | null;
  created_at: string;
}

export const api = {
  health: () => request<{ status: string }>('/health'),

  listTopics: (params?: { status?: string; page?: number; limit?: number }) => {
    const sp = new URLSearchParams();
    if (params?.status) sp.set('status', params.status);
    if (params?.page) sp.set('page', String(params.page));
    if (params?.limit) sp.set('limit', String(params.limit));
    const qs = sp.toString();
    return request<TopicList>(`/api/topics${qs ? '?' + qs : ''}`);
  },

  createTopic: (body: TopicCreate) =>
    request<Topic>('/api/topics', { method: 'POST', body: JSON.stringify(body) }),

  importTopicFromUrl: (body: TopicImportUrl) =>
    request<Topic>('/api/topics/import-url', { method: 'POST', body: JSON.stringify(body) }),

  previewTopicFromUrl: (body: TopicImportUrl) =>
    request<TopicImportPreview>('/api/topics/import-url/preview', { method: 'POST', body: JSON.stringify(body) }),

  confirmTopicImport: (body: TopicImportConfirm) =>
    request<Topic>('/api/topics/import-url/confirm', { method: 'POST', body: JSON.stringify(body) }),

  generateCustomTopicIdeas: (body: CustomTopicIdeasRequest) =>
    request<CustomTopicIdeasResponse>('/api/topics/custom-ideas', { method: 'POST', body: JSON.stringify(body) }),

  confirmCustomTopicIdea: (body: CustomTopicConfirmRequest) =>
    request<Topic>('/api/topics/custom-ideas/confirm', { method: 'POST', body: JSON.stringify(body) }),

  listSources: (params?: { source_type?: string; page?: number; limit?: number }) => {
    const sp = new URLSearchParams();
    if (params?.source_type) sp.set('source_type', params.source_type);
    if (params?.page) sp.set('page', String(params.page));
    if (params?.limit) sp.set('limit', String(params.limit));
    const qs = sp.toString();
    return request<SourceList>(`/api/sources${qs ? '?' + qs : ''}`);
  },

  getSourceStats: () => request<SourceStats>('/api/sources/stats'),

  getSource: (id: number) => request<SourceDetail>(`/api/sources/${id}`),

  generateSourceTopicIdeas: (sourceId: number, body: SourceTopicIdeasRequest) =>
    request<SourceTopicIdeasResponse>(`/api/sources/${sourceId}/topic-ideas`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  confirmSourceTopicIdea: (sourceId: number, body: SourceTopicIdeaConfirmRequest) =>
    request<Topic>(`/api/sources/${sourceId}/topic-ideas/confirm`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getTopic: (id: number) => request<Topic>(`/api/topics/${id}`),

  updateTopic: (id: number, body: Partial<Topic>) =>
    request<Topic>(`/api/topics/${id}`, { method: 'PUT', body: JSON.stringify(body) }),

  deleteTopic: (id: number) =>
    request<void>(`/api/topics/${id}`, { method: 'DELETE' }),

  scoreTopic: (id: number, body = {}) =>
    request<Record<string, unknown>>(`/api/topics/${id}/score`, { method: 'POST', body: JSON.stringify(body) }),

  generateDraft: (topicId: number, body = {}) =>
    request<{ draft: Draft; cards: Card[] }>(`/api/topics/${topicId}/generate-draft`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  listDrafts: (params?: { topic_id?: number; limit?: number }) => {
    const sp = new URLSearchParams();
    if (params?.topic_id) sp.set('topic_id', String(params.topic_id));
    if (params?.limit) sp.set('limit', String(params.limit));
    const qs = sp.toString();
    return request<Draft[]>(`/api/drafts${qs ? '?' + qs : ''}`);
  },

  getLatestDraftByTopic: (topicId: number) =>
    request<Draft>(`/api/drafts/topic/${topicId}/latest`),

  updateDraft: (id: number, body: Partial<Draft>) =>
    request<Draft>(`/api/drafts/${id}`, { method: 'PUT', body: JSON.stringify(body) }),

  deleteDraft: (id: number) =>
    request<void>(`/api/drafts/${id}`, { method: 'DELETE' }),

  createCard: (draftId: number, body?: Partial<Card>) =>
    request<Card[]>(`/api/cards/draft/${draftId}`, {
      method: 'POST',
      body: JSON.stringify({
        card_type: body?.card_type ?? 'concept',
        title: body?.title ?? '新的内容卡',
        subtitle: body?.subtitle ?? null,
        body: body?.body ?? null,
        highlight: body?.highlight ?? null,
        footer: body?.footer ?? '普通人的AI提效实验室',
        layout_key: body?.layout_key ?? 'clean_knowledge',
        theme_key: body?.theme_key ?? 'lab_clean',
        style_json: body?.style_json ?? null,
        page_index: body?.page_index ?? null,
      }),
    }),

  duplicateCard: (cardId: number) =>
    request<Card[]>(`/api/cards/${cardId}/duplicate`, { method: 'POST' }),

  splitCard: (cardId: number) =>
    request<Card[]>(`/api/cards/${cardId}/split`, { method: 'POST' }),

  moveCard: (cardId: number, direction: 'up' | 'down') =>
    request<Card[]>(`/api/cards/${cardId}/move`, {
      method: 'PUT',
      body: JSON.stringify({ direction }),
    }),

  generateDraftVariant: (draftId: number, body: DraftVariantGenerateRequest) =>
    request<DraftVariantResponse>(`/api/drafts/${draftId}/generate-variant`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getReviewChecklist: (draftId: number) =>
    request<ReviewChecklistItem[]>(`/api/drafts/${draftId}/review-checklist`),

  updateReviewChecklist: (draftId: number, items: ReviewChecklistUpdateItem[]) =>
    request<ReviewChecklistItem[]>(`/api/drafts/${draftId}/review-checklist`, {
      method: 'PUT',
      body: JSON.stringify({ items }),
    }),

  listCardsByDraft: (draftId: number) =>
    request<Card[]>(`/api/cards/draft/${draftId}`),

  updateCard: (id: number, body: Partial<Card>) =>
    request<Card>(`/api/cards/${id}`, { method: 'PUT', body: JSON.stringify(body) }),

  deleteCard: (id: number) =>
    request<Card[]>(`/api/cards/${id}`, { method: 'DELETE' }),

  listPublishLogs: () => request<PublishLog[]>('/api/publish-logs'),

  createPublishLog: (body: PublishLogCreate) =>
    request<PublishLog>('/api/publish-logs', { method: 'POST', body: JSON.stringify(body) }),

  listPredictions: (params?: { draft_id?: number; publish_log_id?: number; limit?: number }) => {
    const sp = new URLSearchParams();
    if (params?.draft_id) sp.set('draft_id', String(params.draft_id));
    if (params?.publish_log_id) sp.set('publish_log_id', String(params.publish_log_id));
    if (params?.limit) sp.set('limit', String(params.limit));
    const qs = sp.toString();
    return request<ContentPrediction[]>(`/api/predictions${qs ? '?' + qs : ''}`);
  },

  createPrediction: (body: ContentPredictionCreate) =>
    request<ContentPrediction>('/api/predictions', { method: 'POST', body: JSON.stringify(body) }),

  updatePrediction: (id: number, body: Partial<ContentPredictionCreate>) =>
    request<ContentPrediction>(`/api/predictions/${id}`, { method: 'PUT', body: JSON.stringify(body) }),

  attachPredictionToLog: (id: number, publishLogId: number) =>
    request<ContentPrediction>(`/api/predictions/${id}/attach-log`, {
      method: 'POST',
      body: JSON.stringify({ publish_log_id: publishLogId }),
    }),

  listMetrics: (logId: number) =>
    request<Metric[]>(`/api/publish-logs/${logId}/metrics`),

  createMetric: (logId: number, body: MetricCreate) =>
    request<Metric>(`/api/publish-logs/${logId}/metrics`, { method: 'POST', body: JSON.stringify(body) }),

  listWeeklyReports: () => request<WeeklyReport[]>('/api/reports/weekly'),

  createWeeklyReport: (body: { start_date: string; end_date: string }) =>
    request<WeeklyReport>('/api/reports/weekly', { method: 'POST', body: JSON.stringify(body) }),

  createAgentRun: (body: AgentRunCreate) =>
    request<AgentRunResult>('/api/agent-runs', { method: 'POST', body: JSON.stringify(body) }),

  listAgentRuns: (limit = 20) =>
    request<AgentRun[]>(`/api/agent-runs?limit=${limit}`),

  getAgentRun: (id: number) =>
    request<AgentRunResult>(`/api/agent-runs/${id}`),

  retryAgentRun: (id: number) =>
    request<AgentRunResult>(`/api/agent-runs/${id}/retry`, { method: 'POST', body: JSON.stringify({}) }),

  getArchitecture: () => request<ArchitectureInfo>('/api/v04/architecture'),

  listAgentTools: () => request<AgentToolListResponse>('/api/v04/tools'),

  executeAgentTool: (body: AgentToolExecuteRequest) =>
    request<AgentToolExecuteResponse>('/api/v04/tools/execute', { method: 'POST', body: JSON.stringify(body) }),

  listKnowledgeBases: () => request<KnowledgeBase[]>('/api/v04/knowledge-bases'),

  indexSourceForRag: (body: RagIndexSourceRequest) =>
    request<RagIndexSourceResult>('/api/v04/rag/index-source', { method: 'POST', body: JSON.stringify(body) }),

  searchRag: (body: { query: string; knowledge_base_id?: number | null; top_k?: number }) =>
    request<RagSearchResponse>('/api/v04/rag/search', { method: 'POST', body: JSON.stringify(body) }),

  answerWithRag: (body: { query: string; knowledge_base_id?: number | null; provider?: string; model?: string; top_k?: number }) =>
    request<RagAnswerResponse>('/api/v04/rag/answer', { method: 'POST', body: JSON.stringify(body) }),
};
