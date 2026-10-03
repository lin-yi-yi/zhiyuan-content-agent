import { request } from './client';

export type PilotOutcome = 'pending' | 'accepted' | 'rejected' | 'abandoned';
export interface PilotInput {
  version: number;
  cohort: string;
  outcome: PilotOutcome;
  baseline_minutes: number | null;
  actual_work_minutes: number | null;
  support_minutes: number | null;
  baseline_reference: string;
  acceptance_reference: string;
  note: string;
}
export interface PilotRecord extends PilotInput {
  run_id: number;
  source: 'self_reported';
  content_hash: string | null;
  created_at: string;
  updated_at: string;
}
export interface PilotItem {
  run_id: number;
  goal: string;
  status: string;
  created_at: string;
  record: PilotRecord | null;
  acceptance_current: boolean | null;
}
export interface PilotReport {
  start_date: string;
  end_date: string;
  total_runs: number;
  recorded_runs: number;
  missing_records: number;
  accepted_runs: number;
  stale_acceptances: number;
  rejected_runs: number;
  abandoned_runs: number;
  pending_runs: number;
  comparable_runs: number;
  baseline_minutes_total: number | null;
  actual_minutes_total: number | null;
  saved_minutes_total: number | null;
  savings_rate: number | null;
  items: PilotItem[];
}
export const pilotApi = {
  report: (start: string, end: string, signal?: AbortSignal) => request<PilotReport>(
    `/api/pilot/report?${new URLSearchParams({start_date: start, end_date: end})}`, {signal}),
  record: (id: number) => request<{run_id: number; record: PilotRecord | null}>(`/api/pilot/records/${id}`),
  save: (id: number, body: PilotInput) => request<PilotRecord>(`/api/pilot/records/${id}`, {
    method: 'PUT', body: JSON.stringify(body),
  }),
};
