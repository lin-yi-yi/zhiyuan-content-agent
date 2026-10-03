import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import test from 'node:test';

const exported = {};
const source = readFileSync(new URL('../src/utils/navigation.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
vm.runInNewContext(code, {exports: exported, URLSearchParams});
const {PAGES, parseWorkbenchRoute, routeHash, routeNumber} = exported;

test('品牌进入创作、任务进入指定稿件后刷新，品牌、知识库和版本保持一致', () => {
  for (const value of ['#brands?brand=9', '#rag?kb=12', '#agent?brand=9&kb=12&run=45&workflow=product_faq', '#drafts?draft=7&topic=3&run=45&kb=12']) {
    const route = parseWorkbenchRoute(value);
    const reloaded = parseWorkbenchRoute(routeHash(route));
    assert.equal(reloaded.page, route.page);
    assert.deepEqual([...reloaded.params], [...route.params]);
  }
  const draft = parseWorkbenchRoute('#drafts?draft=7&topic=3&run=45&kb=12&brand=9');
  assert.equal(routeNumber(draft, 'draft'), 7);
  assert.equal(routeNumber(draft, 'run'), 45);
  assert.equal(routeNumber(draft, 'topic'), 3);
  assert.equal(routeNumber(draft, 'kb'), 12);
  assert.equal(routeNumber(draft, 'brand'), 9);
});

test('旧学习、岗位和架构书签回到工作台，丢弃旧页面上下文', () => {
  for (const value of ['learning', '#learning?module=rag&kb=21', 'learning:agent-workflow?run=9', '#portfolio?brand=3', '#architecture?draft=7']) {
    const route = parseWorkbenchRoute(value);
    assert.equal(route.page, 'overview');
    assert.equal(route.params.size, 0);
    assert.equal(routeHash(route), '#overview');
  }
  assert.equal(PAGES.includes('learning'), false);
  assert.equal(PAGES.includes('portfolio'), false);
  assert.equal(PAGES.includes('architecture'), false);
});

test('未知页面和非法外部地址退回首页，不把旧任务 ID 带入首页', () => {
  for (const value of ['', '#missing?run=14&draft=9', '#/rag?kb=2', 'https://evil.invalid/?kb=2', 'javascript:alert(1)']) {
    const route = parseWorkbenchRoute(value);
    assert.equal(route.page, 'overview');
    assert.equal(route.params.size, 0);
    assert.equal(routeHash(route), '#overview');
  }
});

test('品牌和任务 ID 只接受范围内的正整数', () => {
  for (const value of ['0', '-1', '+1', '01', '1.5', '1e3', 'Infinity', 'NaN', '10000000000', '9007199254740992', ' 3', '3 ']) {
    const route = parseWorkbenchRoute(`agent?brand=${encodeURIComponent(value)}&run=8`);
    assert.equal(routeNumber(route, 'brand'), null, `invalid ID: ${value}`);
    assert.equal(routeNumber(route, 'run'), 8);
  }
  assert.equal(routeNumber(parseWorkbenchRoute('agent?run=9999999999'), 'run'), 9999999999);
});

test('只保留业务上下文参数，不在地址中传播凭据、外部跳转或旧学习模块', () => {
  const route = parseWorkbenchRoute('agent?kb=2&brand=7&token=secret&redirect=https%3A%2F%2Fevil.invalid&module=rag');
  assert.deepEqual([...route.params], [['kb', '2'], ['brand', '7']]);
  assert.equal(routeHash(route), '#agent?kb=2&brand=7');
});

test('第二个问号不能将不完整参数误解析为任务或品牌 ID', () => {
  assert.equal(routeNumber(parseWorkbenchRoute('agent?run=5?ignored=9'), 'run'), null);
  assert.equal(routeNumber(parseWorkbenchRoute('brands?brand=7?ignored=9'), 'brand'), null);
});

test('直接构造的地址也会剔除非法 ID 和已经废弃的参数', () => {
  const route = {page: 'agent', params: new URLSearchParams('run=-8&kb=3&token=private&module=rag')};
  assert.equal(routeNumber(route, 'run'), null);
  assert.equal(routeNumber(route, 'module'), null);
  assert.equal(routeNumber(route, 'missing'), null);
  assert.equal(routeHash(route), '#agent?kb=3');
  assert.equal(routeHash({page: 'portfolio', params: new URLSearchParams('run=1')}), '#overview');
});

test('只恢复已支持的工作流模板，未知模板不会绕过选择步骤', () => {
  for (const workflow of ['knowledge_post', 'product_faq', 'case_story']) {
    const route = parseWorkbenchRoute(`agent?workflow=${workflow}&brand=4`);
    assert.equal(parseWorkbenchRoute(routeHash(route)).params.get('workflow'), workflow);
    assert.equal(routeNumber(route, 'workflow'), null);
  }
  for (const value of ['auto_publish', '../../private', '<script>', 'knowledge_post?brand=5']) {
    assert.equal(parseWorkbenchRoute(`agent?workflow=${encodeURIComponent(value)}`).params.has('workflow'), false);
  }
});

test('业务页面可往返，已有素材与选题地址仍可继续使用', () => {
  for (const page of PAGES) {
    assert.equal(parseWorkbenchRoute(routeHash({page, params: new URLSearchParams()})).page, page);
  }
  assert.ok(PAGES.includes('brands'));
  assert.ok(PAGES.includes('sources'));
  assert.ok(PAGES.includes('topics'));
});
