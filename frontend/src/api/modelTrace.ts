import { request } from './client';

export interface ModelCallRecord {
  id: number;
  agent_run_id: number | null;
  agent_step_id: number | null;
  step_key: string | null;
  workflow_attempt: number | null;
  invocation_id: string | null;
  request_index: number | null;
  call_kind: string | null;
  provider: string;
  model_name: string;
  success: boolean;
  latency_ms: number | null;
  prompt_version: string | null;
  prompt_hash: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  error_type: string | null;
  estimated_cost: string | null;
  cost_currency: string | null;
  pricing_version: string | null;
  cost_status: string;
  created_at: string | null;
}

export interface ModelTrace {
  run_id: number;
  generation_mode: string;
  runs: ModelCallRecord[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
  summary: {
    recorded_call_count: number;
    recorded_external_request_count: number;
    local_rule_call_count: number;
    failed_call_count: number;
    unknown_usage_call_count: number;
    total_tokens: number | null;
    estimated_cost: string | null;
    cost_currency: string | null;
    cost_status: string;
    provider_invoice_amount: null;
  };
}

export const getModelTrace = (runId: number, offset = 0) =>
  request<ModelTrace>(`/api/agent-runs/${runId}/model-runs?limit=100&offset=${offset}`);
