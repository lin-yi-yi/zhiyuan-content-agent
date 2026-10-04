import type {QuestionScopeIssueCode, QuestionScopeMethod, QuestionScopeProposal, QuestionScopeProvider, QuestionScopeRequest} from '../api/client';

export const QUESTION_SCOPE_ISSUES: Record<QuestionScopeIssueCode, string> = {
  missing_product: '请补充要核对的产品型号',
  missing_parameter: '请补充要核对的具体参数',
  ambiguous_binding: '请确认每个参数对应哪个型号',
  negation: '问题含否定要求，请手动核对范围',
  unsupported_question: '暂不能整理这类问题，请手动填写',
  too_many_requirements: '要求超过 10 项，请拆成多个问题',
};

export function isQuestionScopeProvider(value: string): value is QuestionScopeProvider {
  return ['deepseek', 'qwen', 'doubao', 'kimi'].includes(value);
}

export function questionScopeRequest(query: string, workspaceId: number | undefined, knowledgeBaseId: number,
  method: QuestionScopeMethod, provider: string): QuestionScopeRequest {
  if (!query.trim() || Array.from(query).length > 1000) throw new Error('请填写不超过 1000 个字符的问题。');
  if (method !== 'rules' && method !== 'model') throw new Error('请选择有效的整理方式。');
  if (method === 'model' && !isQuestionScopeProvider(provider)) throw new Error('请先在回答方式中主动选择可用的在线模型。');
  return {query, workspace_id: workspaceId, knowledge_base_id: knowledgeBaseId, method,
    ...(method === 'model' ? {provider: provider as QuestionScopeProvider} : {})};
}

export function questionScopeContextKey(organizationId: string | null | undefined, request: {
  query: string; workspace_id?: number; knowledge_base_id: number; method: QuestionScopeMethod; provider: string;
}) {
  return JSON.stringify([organizationId ?? null, request.workspace_id ?? null, request.knowledge_base_id,
    request.query, request.method, request.provider]);
}

// Abort is best effort. The generation check also rejects transports/hash work
// that finish after cancellation, including A -> B -> A context changes.
export function createQuestionScopeRequestGuard() {
  let generation = 0;
  let controller: AbortController | null = null;
  const cancel = () => {generation += 1; controller?.abort(); controller = null;};
  return {
    cancel,
    begin: () => {
      cancel();
      controller = new AbortController();
      const current = generation, signal = controller.signal;
      return {signal, isCurrent: () => generation === current && !signal.aborted};
    },
  };
}

const invalidProposal = () => new Error('整理结果与当前问题不匹配或格式无效，请重新整理或手动填写。');
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const exactKeys = (value: Record<string, unknown>, keys: string[]) => {
  const actual = Object.keys(value);
  return actual.length === keys.length && actual.every(key => keys.includes(key));
};

// The server validates the full proposal too. This boundary prevents a stale or
// malformed response from being offered as editable requirements in the page.
export async function validateQuestionScopeProposal(value: unknown, expected: QuestionScopeRequest): Promise<QuestionScopeProposal> {
  if (!record(value) || !exactKeys(value, ['schema_version', 'query_hash', 'method', 'parser_version', 'offset_unit',
    'provider', 'model', 'workspace_id', 'knowledge_base_id', 'status', 'candidates', 'issues', 'requires_confirmation', 'requirements_complete'])) throw invalidProposal();
  const text = Array.from(expected.query);
  const span = (item: unknown, maximum: number) => record(item) && exactKeys(item, ['start', 'end', 'text'])
    && Number.isInteger(item.start) && Number.isInteger(item.end)
    && (item.start as number) >= 0 && (item.end as number) > (item.start as number) && (item.end as number) <= text.length
    && typeof item.text === 'string' && Array.from(item.text).length <= maximum
    && text.slice(item.start as number, item.end as number).join('') === item.text;
  if (value.schema_version !== 1 || value.method !== expected.method
    || value.parser_version !== (expected.method === 'rules' ? 'rules_v1' : 'model_structured_v1')
    || value.offset_unit !== 'unicode_codepoint' || value.requires_confirmation !== true || value.requirements_complete !== false
    || !Number.isSafeInteger(value.workspace_id) || (value.workspace_id as number) < 1
    || !Number.isSafeInteger(value.knowledge_base_id) || (value.knowledge_base_id as number) < 1
    || (expected.workspace_id != null && value.workspace_id !== expected.workspace_id)
    || (expected.knowledge_base_id != null && value.knowledge_base_id !== expected.knowledge_base_id)
    || typeof value.status !== 'string' || !['proposed', 'needs_clarification'].includes(value.status)
    || typeof value.query_hash !== 'string' || !/^[a-f0-9]{64}$/.test(value.query_hash)
    || !Array.isArray(value.candidates) || value.candidates.length > 10
    || !Array.isArray(value.issues) || value.issues.length > 10) throw invalidProposal();
  if (expected.method === 'rules' ? value.provider !== null || value.model !== null
    : value.provider !== expected.provider || typeof value.model !== 'string' || !value.model.trim()
      || (expected.model != null && value.model !== expected.model)) throw invalidProposal();
  for (const item of value.candidates) {
    if (!record(item) || !exactKeys(item, ['product_model', 'parameter', 'product_span', 'parameter_span'])
      || !span(item.product_span, 200) || !span(item.parameter_span, 200)
      || item.product_model !== (item.product_span as Record<string, unknown>).text
      || item.parameter !== (item.parameter_span as Record<string, unknown>).text
      || typeof item.product_model !== 'string' || !item.product_model.trim()
      || typeof item.parameter !== 'string' || !item.parameter.trim()) throw invalidProposal();
  }
  for (const issue of value.issues) {
    if (!record(issue) || !exactKeys(issue, ['code', 'span'])
      || typeof issue.code !== 'string' || !Object.prototype.hasOwnProperty.call(QUESTION_SCOPE_ISSUES, issue.code)
      || !span(issue.span, 1000)) throw invalidProposal();
  }
  if ((value.status === 'proposed' && (!value.candidates.length || value.issues.length))
    || (value.status === 'needs_clarification' && !value.issues.length)) throw invalidProposal();
  let digest: ArrayBuffer;
  try {digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(expected.query));}
  catch {throw new Error('当前浏览器无法校验问题，请使用 HTTPS 或本机地址，或手动填写参数。');}
  const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  if (hash !== value.query_hash) throw invalidProposal();
  return value as unknown as QuestionScopeProposal;
}
