import type { PilotInput, PilotRecord, PilotOutcome } from '../api/pilot';

export interface PilotForm {
  version: number;
  cohort: string;
  outcome: PilotOutcome;
  baseline_minutes: string;
  actual_work_minutes: string;
  support_minutes: string;
  baseline_reference: string;
  acceptance_reference: string;
  note: string;
}
export function recordToForm(record: PilotRecord | null): PilotForm {
  return {
    version: record?.version ?? 0,
    cohort: record?.cohort ?? '首轮试点',
    outcome: record?.outcome ?? 'pending',
    baseline_minutes: record?.baseline_minutes == null ? '' : String(record.baseline_minutes),
    actual_work_minutes: record?.actual_work_minutes == null ? '' : String(record.actual_work_minutes),
    support_minutes: record?.support_minutes == null ? '' : String(record.support_minutes),
    baseline_reference: record?.baseline_reference ?? '',
    acceptance_reference: record?.acceptance_reference ?? '',
    note: record?.note ?? '',
  };
}
export function formToInput(form: PilotForm): PilotInput {
  const minutes = (value: string, label: string, positive = false) => {
    if (!value.trim()) return null;
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0 || (positive && number < 0.01) || number > 100000) {
      throw new Error(`${label}须为${positive ? '至少 0.01' : '不小于零'}的有效分钟数（最多 100000）。`);
    }
    return number;
  };
  const input: PilotInput = {
    ...form,
    cohort: form.cohort.trim(),
    baseline_reference: form.baseline_reference.trim(),
    acceptance_reference: form.acceptance_reference.trim(),
    note: form.note.trim(),
    baseline_minutes: minutes(form.baseline_minutes, '原流程基线', true),
    actual_work_minutes: minutes(form.actual_work_minutes, '实际操作工时'),
    support_minutes: minutes(form.support_minutes, '实施支持工时'),
  };
  if (!input.cohort) throw new Error('请填写试点批次。');
  if (input.baseline_minutes !== null && !input.baseline_reference) throw new Error('填写基线工时后，请补充同类任务的对照依据。');
  if (input.outcome === 'accepted' && !input.acceptance_reference) throw new Error('登记客户验收通过时，请填写可回查的确认依据。');
  return input;
}
