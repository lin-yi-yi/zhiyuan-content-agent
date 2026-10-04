import {useEffect, useRef, useState} from 'react';
import {api, QuestionScopeMethod, QuestionScopeProposal as Proposal, QuestionScopeSpan, RequiredFact} from '../api/client';
import {createQuestionScopeRequestGuard, isQuestionScopeProvider, QUESTION_SCOPE_ISSUES,
  questionScopeContextKey, questionScopeRequest, validateQuestionScopeProposal} from '../utils/questionScope';
import RequestFailure from './RequestFailure';

type ProposalState = {key: string} & ({phase: 'loading'} | {phase: 'ready'; proposal: Proposal} | {phase: 'error'; error: Error | string});

export default function QuestionScopeProposal({query, organizationId, workspaceId, knowledgeBaseId, provider,
  modelLabel, disabled, onAdd}: {query: string; organizationId?: string | null; workspaceId?: number;
  knowledgeBaseId: number; provider: string; modelLabel: string; disabled: boolean; onAdd: (fact: RequiredFact) => void}) {
  const [method, setMethod] = useState<QuestionScopeMethod>('rules');
  const [state, setState] = useState<ProposalState | null>(null);
  const guard = useRef(createQuestionScopeRequestGuard());
  const key = questionScopeContextKey(organizationId, {query, workspace_id: workspaceId, knowledge_base_id: knowledgeBaseId, method, provider});
  const latestKey = useRef(key);
  latestKey.current = key;
  const visible = state?.key === key ? state : null;
  const busy = visible?.phase === 'loading';
  const result = visible?.phase === 'ready' ? visible.proposal : null;
  const modelAllowed = isQuestionScopeProvider(provider);
  const original = (span: QuestionScopeSpan, label: string) => {
    const characters = Array.from(query);
    return <div>
      <p className="subtle">{label} · 第 {span.start + 1} 至 {span.end} 个字符</p>
      <p style={{whiteSpace: 'pre-wrap', overflowWrap: 'anywhere'}}>{characters.slice(0, span.start).join('')}
        <mark>{characters.slice(span.start, span.end).join('')}</mark>{characters.slice(span.end).join('')}</p>
    </div>;
  };

  useEffect(() => {
    guard.current.cancel();
    setState(null);
    return () => guard.current.cancel();
  }, [key]);

  const propose = async () => {
    const token = guard.current.begin();
    const current = () => token.isCurrent() && latestKey.current === key;
    setState({key, phase: 'loading'});
    try {
      const body = questionScopeRequest(query, workspaceId, knowledgeBaseId, method, provider);
      const response = await api.proposeQuestionScope(body, token.signal);
      if (!current()) return;
      const proposal = await validateQuestionScopeProposal(response, body);
      if (current()) setState({key, phase: 'ready', proposal});
    } catch (error) {
      if (current()) {
        // Session/CSRF renewal can cancel the shared client without changing
        // this page's scope key. End the spinner without showing stale errors.
        setState(error instanceof Error && error.name === 'AbortError' ? null
          : {key, phase: 'error', error: error instanceof Error ? error : '整理失败，请重试或手动填写。'});
      }
    }
  };

  return <div className="rag-catalog" aria-label="从问题整理参数">
    <div className="form-group">
      <label htmlFor="rag-scope-method">整理方式</label>
      <select id="rag-scope-method" disabled={disabled} value={method} onChange={event => setMethod(event.target.value as QuestionScopeMethod)}>
        <option value="rules">本地规则 · 无模型调用</option>
        <option value="model" disabled={!modelAllowed}>{modelAllowed ? `当前回答模型 · ${modelLabel}` : '在线模型 · 请先选择非本地回答方式'}</option>
      </select>
      <p className="subtle">{method === 'rules' ? '仅整理原问题中的明确表述，不调用模型、不消耗 AI 请求额度。'
        : `将原问题发送给 ${modelLabel} 整理，不发送资料；这次调用可能产生额外费用并消耗 AI 请求额度。失败时不会自动改用规则。`}</p>
    </div>
    <button type="button" className="btn" disabled={disabled || busy || !query.trim() || (method === 'model' && !modelAllowed)} onClick={() => void propose()}>
      {busy ? '正在整理参数…' : '从问题整理参数'}
    </button>
    <p className="subtle">整理结果只是待核对的提案，不保证需求完整，也不代表资料已有依据。逐项加入清单后，仍需对照原问题确认；可编辑为资料登记名称。</p>
    {visible?.phase === 'error' && <RequestFailure error={visible.error}/>}
    {result && <div className="rag-catalog-body">
      <p role="status">{result.status === 'needs_clarification' ? '有需要澄清的内容，请核对下方原文并补充清单。' : '已整理候选，请逐项核对原文。'}</p>
      <ul className="rag-catalog-items">
        {result.candidates.map((candidate, index) => <li key={index}>
          <p style={{overflowWrap: 'anywhere'}}>型号原文：{candidate.product_span.text}<br/>参数原文：{candidate.parameter_span.text}</p>
          <details><summary>查看原文位置</summary>{original(candidate.product_span, '型号位置')}{original(candidate.parameter_span, '参数位置')}</details>
          <button type="button" className="btn" disabled={disabled} onClick={() => onAdd({product_model: candidate.product_model, parameter: candidate.parameter})}>
            加入提案 {candidate.product_model} / {candidate.parameter}
          </button>
        </li>)}
      </ul>
      {result.issues.length > 0 && <ul className="rag-catalog-items">
        {result.issues.map((issue, index) => <li key={index} style={{overflowWrap: 'anywhere'}}>
          <strong>{QUESTION_SCOPE_ISSUES[issue.code]}</strong><p>问题原文：{issue.span.text}</p>
          <details><summary>查看需澄清的原文位置</summary>{original(issue.span, '需澄清的位置')}</details>
        </li>)}
      </ul>}
    </div>}
  </div>;
}
