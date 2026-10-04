import assert from 'node:assert/strict';
import {createHash, webcrypto} from 'node:crypto';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import test from 'node:test';

const plain = value => JSON.parse(JSON.stringify(value));
function load(relative, dependencies = {}, globals = {}) {
  const output = {};
  const code = ts.transpileModule(readFileSync(new URL(relative, import.meta.url), 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX},
  }).outputText;
  vm.runInNewContext(code, {exports: output, require: path => {
    if (path.endsWith('.css')) return {};
    assert.ok(path in dependencies, `unmocked import ${path}`); return dependencies[path];
  }, AbortController, DOMException, Error, Headers, TextEncoder, Uint8Array, crypto: webcrypto, ...globals});
  return output;
}
const scope = load('../src/utils/questionScope.ts');
const facts = load('../src/utils/ragFacts.ts');
const QUERY = '  合成 XP-24 的电压和售价是多少？  ';
const body = (query = QUERY) => ({query, workspace_id: 7, knowledge_base_id: 12, method: 'rules'});
const span = (query, text) => {
  const prefix = query.slice(0, query.indexOf(text));
  return {start: Array.from(prefix).length, end: Array.from(prefix + text).length, text};
};
function proposal(query = QUERY, product = '合成 XP-24', parameter = '售价') {
  return {schema_version: 1, query_hash: createHash('sha256').update(query).digest('hex'), method: 'rules',
    parser_version: 'rules_v1', offset_unit: 'unicode_codepoint', provider: null, model: null,
    workspace_id: 7, knowledge_base_id: 12, status: 'proposed', candidates: [{product_model: product, parameter,
      product_span: span(query, product), parameter_span: span(query, parameter)}], issues: [],
    requires_confirmation: true, requirements_complete: false};
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}

test('规则请求原样发送问题并省略模型字段；在线整理必须显式指定支持的非本地 provider', () => {
  assert.deepEqual(plain(scope.questionScopeRequest(QUERY, 7, 12, 'rules', 'deepseek')), body());
  assert.deepEqual(plain(scope.questionScopeRequest(QUERY, 7, 12, 'model', 'qwen')), {...body(), method: 'model', provider: 'qwen'});
  for (const provider of ['local', '', 'unregistered']) assert.throws(() => scope.questionScopeRequest(QUERY, 7, 12, 'model', provider), /主动选择/);
  for (const query of ['', '  ', '甲'.repeat(1001)]) assert.throws(() => scope.questionScopeRequest(query, 7, 12, 'rules', 'local'), /1000/);
  assert.equal(scope.questionScopeRequest('😀'.repeat(1000), 7, 12, 'rules', 'local').query.length, 2000);
});

test('原问题空白参与 hash，原文 span 使用 Unicode codepoint，保留未知型号及单位大小写', async () => {
  for (const parameter of ['mA', 'MA', '售价']) {
    const query = `  😀 未登记 U-99 的${parameter}是多少？  `;
    const result = await scope.validateQuestionScopeProposal(proposal(query, '未登记 U-99', parameter), body(query));
    assert.equal(result.candidates[0].product_model, '未登记 U-99');
    assert.equal(result.candidates[0].parameter, parameter);
  }
  await assert.rejects(scope.validateQuestionScopeProposal(proposal(), body(QUERY.trim())), /不匹配或格式无效/);
});

test('澄清项只展示有定位的原文，不捏造候选，complete 始终为 false', async () => {
  const value = proposal(); value.candidates = []; value.status = 'needs_clarification';
  value.issues = [{code: 'missing_product', span: span(QUERY, '售价')}];
  const result = await scope.validateQuestionScopeProposal(value, body());
  assert.equal(result.candidates.length, 0); assert.equal(result.requires_confirmation, true);
  assert.equal(result.requirements_complete, false);
});

for (const [name, change] of Object.entries({
  'hash 错误': p => {p.query_hash = 'a'.repeat(64);},
  '知识库错误': p => {p.knowledge_base_id = 13;},
  '工作区错误': p => {p.workspace_id = 8;},
  '非整数范围': p => {p.workspace_id = 7.2;},
  'schema 版本错误': p => {p.schema_version = 2;},
  '数组伪装状态': p => {p.status = ['proposed'];},
  '自动确认': p => {p.requires_confirmation = false;},
  '伪造完整性': p => {p.requirements_complete = true;},
  '错误坐标单位': p => {p.offset_unit = 'utf16';},
  '规则冒充模型': p => {p.parser_version = 'model_structured_v1';},
  '规则携带 provider': p => {p.provider = 'deepseek';},
  '未知顶层字段': p => {p.raw_model_output = 'SECRET_MARKER';},
  '型号擅自补品牌': p => {p.candidates[0].product_model = '另外品牌 合成 XP-24';},
  '参数擅自补额定': p => {p.candidates[0].parameter = '额定售价';},
  '原文错误': p => {p.candidates[0].product_span.text = 'SECRET_MARKER';},
  '小数位置': p => {p.candidates[0].product_span.start += 0.5;},
  '负数位置': p => {p.candidates[0].product_span.start = -1;},
  '超原文位置': p => {p.candidates[0].product_span.end = 99999;},
  '空定位': p => {p.candidates[0].product_span.end = p.candidates[0].product_span.start;},
  '未知候选字段': p => {p.candidates[0].confirmed = true;},
  '半填候选': p => {delete p.candidates[0].parameter;},
  '候选超十项': p => {p.candidates = Array(11).fill(p.candidates[0]);},
  '空提案未澄清': p => {p.candidates = [];},
  '澄清无原因': p => {p.status = 'needs_clarification';},
  '未知问题码': p => {p.status = 'needs_clarification';p.issues = [{code: '__proto__', span: span(QUERY, '售价')}];},
  '澄清超过十项': p => {p.status = 'needs_clarification';p.issues = Array(11).fill({code: 'missing_product', span: span(QUERY, '售价')});},
})) test(`整份坏提案拒收且错误不回显原始响应：${name}`, async () => {
  const value = proposal(); change(value);
  await assert.rejects(scope.validateQuestionScopeProposal(value, body()), error => /不匹配或格式无效/.test(error.message) && !error.message.includes('SECRET_MARKER'));
});

test('在线响应绑定请求 provider/model；无明确 model 时接受实际配置型号', async () => {
  const expected = {...body(), method: 'model', provider: 'qwen'};
  const value = {...proposal(), method: 'model', parser_version: 'model_structured_v1', provider: 'qwen', model: 'configured-model'};
  assert.equal((await scope.validateQuestionScopeProposal(value, expected)).model, 'configured-model');
  await assert.rejects(scope.validateQuestionScopeProposal({...value, provider: 'kimi'}, expected), /不匹配/);
  await assert.rejects(scope.validateQuestionScopeProposal(value, {...expected, model: 'another-model'}), /不匹配/);
});

test('字段最长 200 codepoint，错误浏览器 hash 能力保持手动路径', async () => {
  const product = '😀'.repeat(200), query = `${product} 的售价是多少？`;
  assert.equal((await scope.validateQuestionScopeProposal(proposal(query, product), body(query))).candidates.length, 1);
  const long = '😀'.repeat(201), longQuery = `${long} 的售价是多少？`;
  await assert.rejects(scope.validateQuestionScopeProposal(proposal(longQuery, long), body(longQuery)), /不匹配/);
  const unavailable = load('../src/utils/questionScope.ts', {}, {crypto: {}});
  await assert.rejects(unavailable.validateQuestionScopeProposal(proposal(), body()), /手动填写/);
});

test('已取消的代次不会因同样问题重新请求而复活', () => {
  const guard = scope.createQuestionScopeRequestGuard();
  const a = guard.begin(); guard.cancel(); const b = guard.begin(); const againA = guard.begin();
  assert.equal(a.signal.aborted, true); assert.equal(a.isCurrent(), false);
  assert.equal(b.signal.aborted, true); assert.equal(againA.isCurrent(), true);
  guard.cancel(); assert.equal(againA.isCurrent(), false);
});

// Run actual component callbacks/effects with a minimal hook scheduler. No copy
// of the proposal logic, network, DOM or additional testing dependency is used.
function renderComponent(relative, dependencies, initialProps) {
  const slots = []; let cursor = 0, effects = [], dirty = true, props = initialProps, tree, unmounted = false;
  const react = {
    useState(initial) {const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], next => {const value = typeof next === 'function' ? next(slots[index]) : next;
        if (!Object.is(value, slots[index])) {slots[index] = value; dirty = true;}}];},
    useRef(initial) {const index = cursor++; if (!(index in slots)) slots[index] = {current: initial}; return slots[index];},
    useEffect(callback, deps) {const index = cursor++, previous = slots[index];
      if (!previous || !deps || deps.some((value, i) => !Object.is(value, previous.deps[i]))) {
        effects.push(() => {previous?.cleanup?.(); slots[index] = {deps, cleanup: callback()};});
      }},
  };
  const jsx = (type, props, key) => ({type, props: props || {}, key});
  const component = load(relative, {...dependencies, react, 'react/jsx-runtime': {jsx, jsxs: jsx, Fragment: 'fragment'}}).default;
  const render = () => {
    let count = 0;
    while (dirty && !unmounted) {
      assert.ok(count++ < 25, 'render effect loop'); dirty = false; cursor = 0; effects = [];
      tree = component(props); effects.forEach(run => run());
    }
    return tree;
  };
  const nodes = () => {
    render(); const all = [];
    const visit = value => {if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === 'object' && 'type' in value) {all.push(value);visit(value.props.children);}};
    visit(tree); return all;
  };
  const text = value => Array.isArray(value) ? value.map(text).join('')
    : value && typeof value === 'object' ? text(value.props?.children) : String(value ?? '');
  return {render, nodes, text,
    find: predicate => {const result = nodes().find(predicate); assert.ok(result, 'expected rendered element'); return result;},
    props(next) {props = {...props, ...next}; dirty = true; render();},
    async settle() {for (let i = 0; i < 6; i++) {await new Promise(resolve => setTimeout(resolve, 1)); render();}},
    unmount() {slots.forEach(slot => slot?.cleanup?.());unmounted = true;},
  };
}
const initialProps = {query: QUERY, organizationId: 'team-a', workspaceId: 7, knowledgeBaseId: 12,
  provider: 'local', modelLabel: 'local', disabled: false, onAdd() {}};
function proposalComponent(transport, extra = {}) {
  return renderComponent('../src/components/QuestionScopeProposal.tsx', {
    '../api/client': {api: {proposeQuestionScope: transport}}, '../utils/questionScope': scope,
    './RequestFailure': {default: 'RequestFailure'},
  }, {...initialProps, ...extra});
}
const proposeButton = component => component.find(node => node.type === 'button' && /从问题整理参数|正在整理参数/.test(component.text(node)));

test('默认本地规则，即使主回答模型在线也不自动发模型请求；候选到达不自动加入或确认', async () => {
  const calls = [], added = [];
  const ui = proposalComponent(async request => {calls.push(request); return proposal();}, {provider: 'qwen', modelLabel: 'qwen-test', onAdd: fact => added.push(fact)});
  assert.equal(ui.find(n => n.props.id === 'rag-scope-method').props.value, 'rules');
  assert.equal(calls.length, 0); proposeButton(ui).props.onClick(); await ui.settle();
  assert.equal(calls[0].method, 'rules'); assert.equal(calls[0].provider, undefined); assert.equal(added.length, 0);
  const add = ui.find(n => n.type === 'button' && ui.text(n).startsWith('加入提案'));
  add.props.onClick(); assert.deepEqual(plain(added), [{product_model: '合成 XP-24', parameter: '售价'}]);
});

test('必须主动选择在线整理；费用与只发送问题提示明确，503 不回退规则', async () => {
  const calls = [], failure = new Error('所选模型暂不可用');
  const ui = proposalComponent(async request => {calls.push(request);throw failure;}, {provider: 'qwen', modelLabel: 'qwen-test'});
  ui.find(n => n.props.id === 'rag-scope-method').props.onChange({target: {value: 'model'}});
  assert.ok(ui.nodes().some(n => n.type === 'p' && /额外费用/.test(ui.text(n)) && /不发送资料/.test(ui.text(n))));
  proposeButton(ui).props.onClick(); await ui.settle();
  assert.equal(calls.length, 1); assert.equal(calls[0].provider, 'qwen'); assert.equal(calls[0].method, 'model');
  assert.equal(ui.find(n => n.type === 'RequestFailure').props.error, failure);
  const local = proposalComponent(async () => assert.fail('must not call'));
  assert.equal(local.find(n => n.type === 'option' && n.props.value === 'model').props.disabled, true);
});

for (const [label, change] of Object.entries({问题: {query: '另外问题'}, 知识库: {knowledgeBaseId: 13},
  工作区: {workspaceId: 8}, 组织: {organizationId: 'team-b'}, 模型: {provider: 'qwen'},
})) test(`切换${label}清提案并拒绝迟到成功与错误`, async () => {
  for (const failed of [false, true]) {
    const slow = deferred(); let signal;
    const ui = proposalComponent(async (_body, value) => {signal = value;return slow.promise;});
    proposeButton(ui).props.onClick(); ui.props(change); assert.equal(signal.aborted, true);
    failed ? slow.reject(new Error('旧范围私有错误')) : slow.resolve(proposal());
    await ui.settle();
    assert.equal(ui.nodes().some(n => n.type === 'RequestFailure' || n.type === 'button' && ui.text(n).startsWith('加入提案')), false);
    assert.equal(proposeButton(ui).props.disabled, false);
  }
});

test('问题 A→B→A 后旧提案不复活；卸载后请求取消', async () => {
  const first = deferred(), second = deferred(); let count = 0, signal;
  const ui = proposalComponent(async (_body, value) => {signal = value;return (++count === 1 ? first : second).promise;});
  proposeButton(ui).props.onClick(); ui.props({query: '问题 B'}); ui.props({query: QUERY});
  proposeButton(ui).props.onClick(); first.resolve(proposal()); await ui.settle();
  assert.equal(ui.nodes().some(n => n.type === 'button' && ui.text(n).startsWith('加入提案')), false);
  assert.equal(proposeButton(ui).props.disabled, true);
  ui.unmount(); assert.equal(signal.aborted, true); second.resolve(proposal()); await ui.settle();
});

test('同组织会话刷新导致客户端取消时停止等待，不显示过期错误', async () => {
  const ui = proposalComponent(async () => {throw new DOMException('请求已取消', 'AbortError');});
  proposeButton(ui).props.onClick(); await ui.settle();
  assert.equal(proposeButton(ui).props.disabled, false);
  assert.equal(ui.nodes().some(n => n.type === 'RequestFailure'), false);
});

test('坏 hash 不出现加入按钮，澄清问题保留固定提示和原文', async () => {
  const bad = proposal(); bad.query_hash = '0'.repeat(64);
  const ui = proposalComponent(async () => bad); proposeButton(ui).props.onClick(); await ui.settle();
  assert.match(ui.find(n => n.type === 'RequestFailure').props.error.message, /不匹配/);
  assert.equal(ui.nodes().some(n => n.type === 'button' && ui.text(n).startsWith('加入提案')), false);
  const unclear = {...proposal(), candidates: [], status: 'needs_clarification', issues: [{code: 'missing_product', span: span(QUERY, '售价')}]};
  const other = proposalComponent(async () => unclear); proposeButton(other).props.onClick(); await other.settle();
  assert.ok(other.nodes().some(n => n.type === 'strong' && /补充.*产品型号/.test(other.text(n))));
  assert.ok(other.nodes().some(n => n.type === 'p' && other.text(n) === '问题原文：售价'));
});

test('原文高亮依据具体 span，重复参数与 emoji 不会错位', async () => {
  const query = '😀 合成 XP-24 的售价和售价是多少？';
  const value = proposal(query);
  const start = Array.from(query.slice(0, query.lastIndexOf('售价'))).length;
  value.candidates[0].parameter_span = {start, end: start + 2, text: '售价'};
  const ui = proposalComponent(async () => value, {query});
  proposeButton(ui).props.onClick(); await ui.settle();
  const marks = ui.nodes().filter(n => n.type === 'mark');
  assert.deepEqual(marks.map(mark => ui.text(mark)), ['合成 XP-24', '售价']);
  assert.ok(ui.nodes().some(n => n.type === 'p' && ui.text(n) === `参数位置 · 第 ${start + 1} 至 ${start + 2} 个字符`));
  const paragraphs = ui.nodes().filter(n => n.type === 'p' && n.props.style?.whiteSpace === 'pre-wrap');
  assert.deepEqual(paragraphs.map(n => ui.text(n)), [query, query]);
});

test('真实 RagLab 加入候选保留手工未知项、半填项；所有修改撤销确认，最终发送用户确认的完整清单', async () => {
  const models = [{provider: 'local', model: 'local-rule-based-v0'}], sent = [];
  const ui = renderComponent('../src/pages/RagLabPage.tsx', {
    '../api/client': {api: {listKnowledgeBases: async () => [{id: 12, name: '合成库'}],
      answerWithRag: async value => {sent.push(value); return {answer: '', answerability: 'missing_required_facts', required_facts: value.required_facts, missing_facts: value.required_facts, citations: []};}}},
    '../api/workbench': {workbench: {status: async () => ({mode: 'lexical'})}},
    '../components/EvidenceProvenance': {default: 'EvidenceProvenance'}, '../api/evidence': {safeEvidenceUrl: () => null},
    '../components/WorkspaceContext': {useWorkspace: () => ({canWrite: true, organization: {id: 'team-a'}})},
    '../components/useAvailableModels': {useAvailableModels: () => ({models})}, '../utils/ragFacts': facts,
    '../components/RagFactCatalogPicker': {default: 'RagFactCatalogPicker'},
    '../components/QuestionScopeProposal': {default: 'QuestionScopeProposal'}, '../components/RequestFailure': {default: 'RequestFailure'},
  }, {initialScope: {workspace_id: 7}, initialKnowledgeBaseId: 12});
  await ui.settle();
  const fill = (id, value) => ui.find(n => n.props.id === id).props.onChange({target: {value}});
  fill('rag-question', QUERY); fill('rag-model-0', '手填未知型号');
  ui.find(n => n.type === 'QuestionScopeProposal').props.onAdd({product_model: '合成 XP-24', parameter: '售价'});
  assert.equal(ui.find(n => n.props.id === 'rag-model-0').props.value, '手填未知型号');
  assert.equal(ui.find(n => n.props.id === 'rag-parameter-0').props.value, '');
  assert.equal(ui.find(n => n.props.id === 'rag-model-1').props.value, '合成 XP-24');
  fill('rag-parameter-0', '目录没有的 mA');
  const confirm = () => ui.find(n => n.type === 'input' && n.props.type === 'checkbox');
  confirm().props.onChange({target: {checked: true}}); assert.equal(confirm().props.checked, true);
  ui.find(n => n.type === 'QuestionScopeProposal').props.onAdd({product_model: '合成 XP-24', parameter: '电压'});
  assert.equal(confirm().props.checked, false);
  const answer = () => ui.find(n => n.type === 'button' && ui.text(n).includes('按确认参数提问'));
  assert.equal(answer().props.disabled, true); assert.equal(sent.length, 0);
  confirm().props.onChange({target: {checked: true}}); answer().props.onClick(); await ui.settle();
  assert.deepEqual(plain(sent[0].required_facts), [{product_model: '手填未知型号', parameter: '目录没有的 mA'},
    {product_model: '合成 XP-24', parameter: '售价'}, {product_model: '合成 XP-24', parameter: '电压'}]);
  fill('rag-question', QUERY + '？'); assert.equal(confirm().props.checked, false);
});

test('新 API 复用组织/CSRF、原问题和取消保护；不发送资料或目录', async () => {
  let sent; const slow = deferred();
  const client = load('../src/api/client.ts', {}, {window: {dispatchEvent() {}}, Event,
    fetch: async (url, opts) => {sent = {url, opts}; return slow.promise;}});
  client.configureRequestContext('team-a', 'csrf-a');
  const controller = new AbortController(), pending = client.api.proposeQuestionScope(body(), controller.signal);
  assert.equal(sent.url, '/api/v04/rag/question-scope');
  assert.equal(sent.opts.headers.get('X-Organization-ID'), 'team-a');
  assert.equal(sent.opts.headers.get('X-CSRF-Token'), 'csrf-a');
  assert.deepEqual(JSON.parse(sent.opts.body), body());
  client.configureRequestContext('team-b', 'csrf-b'); assert.equal(sent.opts.signal.aborted, true);
  slow.resolve({ok: true, status: 200, json: async () => proposal()});
  await assert.rejects(pending, error => error.name === 'AbortError');
});
