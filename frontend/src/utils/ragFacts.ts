import type { RagAnswerResponse, RequiredFact } from '../api/client';

export interface RagQuestionScope {
  knowledgeBaseId: number | null;
  productModel: string;
  parameters: string;
}

export function selectRagKnowledgeBase(current: RagQuestionScope, knowledgeBaseId: number | null): RagQuestionScope {
  return current.knowledgeBaseId === knowledgeBaseId
    ? current
    : {knowledgeBaseId, productModel: '', parameters: ''};
}

// Keep user-entered parameter names, including names not registered in evidence.
// Semantic matching and source eligibility are exclusively checked by the API.
export function parseRagRequiredFacts(productModel: string, parameters: string): RequiredFact[] {
  const model = productModel.trim();
  const names = parameters.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  if (!model && !names.length) return [];
  if (!model || !names.length) throw new Error('请同时填写产品型号和至少一个本次必需参数，或将两项都留空。');
  if ([...model].length > 200) throw new Error('产品型号不能超过 200 个字符。');
  if (names.length > 10) throw new Error('本次最多填写 10 个必需参数，每行一个。');
  if (names.some(name => [...name].length > 200)) throw new Error('每个参数名称不能超过 200 个字符。');
  return names.map(parameter => ({product_model: model, parameter}));
}

// Each row is one requirement; never split punctuation or silently discard a
// half-filled row. Empty rows are merely UI placeholders, not requirements.
export function validateRagFactRows(rows: RequiredFact[]): RequiredFact[] {
  const filled = rows.map(row => ({product_model: row.product_model.trim(), parameter: row.parameter.trim()}))
    .filter(row => row.product_model || row.parameter);
  if (filled.length > 10) throw new Error('本次最多填写 10 个必需参数。');
  if (filled.some(row => !row.product_model || !row.parameter)) throw new Error('请为每一行同时填写产品型号和参数，或删除该行。');
  if (filled.some(row => [...row.product_model].length > 200 || [...row.parameter].length > 200)) throw new Error('每个型号和参数名称不能超过 200 个字符。');
  return filled;
}

const factKey = (fact: RequiredFact) => [fact.product_model, fact.parameter]
  .map(value => value.normalize('NFKC').replace(/\s+/gu, '')).join('\u0000');

export function addRagCatalogFact(rows: RequiredFact[], fact: RequiredFact): RequiredFact[] {
  if (rows.some(row => factKey(row) === factKey(fact))) return rows;
  const copy = rows.map(row => ({...row}));
  const empty = copy.findIndex(row => !row.product_model.trim() && !row.parameter.trim());
  if (empty >= 0) copy[empty] = {product_model: fact.product_model, parameter: fact.parameter};
  else {
    if (copy.length >= 10) throw new Error('已达到 10 项，请先删除不需要的参数。');
    copy.push({product_model: fact.product_model, parameter: fact.parameter});
  }
  return copy;
}

export function ragFactCoverage(answer: Pick<RagAnswerResponse, 'answerability' | 'required_facts' | 'missing_facts'>) {
  const missing = answer.missing_facts || [];
  // Fail conservatively if a response contains missing facts with a stale status.
  if (missing.length || answer.answerability === 'missing_required_facts') {
    return {
      label: '参数覆盖：缺少必需依据',
      explanation: '以下参数在本次可用检索片段中缺少已核验证据；请补充带版本、参数值和原文定位的资料后重试。',
      facts: missing,
    };
  }
  if (answer.answerability === 'required_facts_present' && answer.required_facts?.length) {
    return {
      label: '参数覆盖：所列参数均有依据',
      explanation: '仅核对下列参数在有效检索片段中的覆盖；仍需核对适用条件，未列出的要求和生成结论不在此检查范围内。',
      facts: answer.required_facts,
    };
  }
  return {
    label: '参数覆盖：未评估',
    explanation: '当前结果不能确认所问参数是否齐全。相关片段不代表问题可完整回答。',
    facts: [],
  };
}
