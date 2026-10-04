import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import test from 'node:test';

const exported = {};
const source = readFileSync(new URL('../src/utils/ragFacts.ts', import.meta.url), 'utf8');
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020},
}).outputText, {exports: exported});
const {parseRagRequiredFacts, selectRagKnowledgeBase, ragFactCoverage, validateRagFactRows, addRagCatalogFact} = exported;
const plain = value => JSON.parse(JSON.stringify(value));

test('手填参数原样保留：正常、缺价、响应时间与复合问题均可送往服务端核验', () => {
  for (const names of [
    ['额定电压 / 直流输入'], ['售价'], ['响应时间'],
    ['额定电压 / 直流输入', '售价'], ['任意新产品的未登记参数'],
  ]) {
    assert.deepEqual(plain(parseRagRequiredFacts(' 合成星桥 XP-24 ', `\n ${names.join(' \r\n ')} \n`)),
      names.map(parameter => ({product_model: '合成星桥 XP-24', parameter})));
  }
  // Do not split a parameter's slash, comma, or semicolon into invented keys.
  assert.equal(parseRagRequiredFacts('厂牌 α / B', '宽度, 高度；公差')[0].parameter, '宽度, 高度；公差');
});

test('留空明确不评估；半填不能静默丢弃必需参数', () => {
  assert.deepEqual(plain(parseRagRequiredFacts('  ', '\n\r\n')), []);
  assert.throws(() => parseRagRequiredFacts('XP-24', ' \n '), /同时填写/);
  assert.throws(() => parseRagRequiredFacts(' ', '售价'), /同时填写/);
});

test('参数数量及名称长度遵守 API 限制，不截断用户的要求', () => {
  assert.equal(parseRagRequiredFacts('x'.repeat(200), Array.from({length: 10}, (_, i) => `参数${i}`).join('\n')).length, 10);
  assert.equal(parseRagRequiredFacts('型号', 'x'.repeat(200))[0].parameter.length, 200);
  assert.throws(() => parseRagRequiredFacts('x'.repeat(201), '电压'), /200/);
  assert.throws(() => parseRagRequiredFacts('型号', 'x'.repeat(201)), /200/);
  assert.throws(() => parseRagRequiredFacts('型号', Array.from({length: 11}, (_, i) => `参数${i}`).join('\n')), /10/);
});

test('切换或清空知识库清除旧型号与参数；同库刷新保留正在填写的要求', () => {
  const first = {knowledgeBaseId: 1, productModel: '合成星桥 XP-24', parameters: '额定电压\n售价'};
  assert.equal(selectRagKnowledgeBase(first, 1), first);
  assert.deepEqual(plain(selectRagKnowledgeBase(first, 2)), {knowledgeBaseId: 2, productModel: '', parameters: ''});
  assert.deepEqual(plain(selectRagKnowledgeBase(first, null)), {knowledgeBaseId: null, productModel: '', parameters: ''});
  assert.equal(first.productModel, '合成星桥 XP-24');
});

test('复合问题只列实际缺项，相关片段充足不能掩盖缺项状态', () => {
  const voltage = {product_model: '合成星桥 XP-24', parameter: '额定电压'};
  const price = {product_model: '合成星桥 XP-24', parameter: '售价'};
  const status = ragFactCoverage({answerability: 'missing_required_facts', required_facts: [voltage, price], missing_facts: [price], coverage: {status: 'sufficient'}});
  assert.equal(status.label, '参数覆盖：缺少必需依据');
  assert.deepEqual(plain(status.facts), [price]);
  assert.match(status.explanation, /本次可用检索片段/);
});

test('已核对、未评估与响应缺字段显示不同口径，不将覆盖称为回答正确', () => {
  const facts = [{product_model: '型号', parameter: '参数'}];
  const present = ragFactCoverage({answerability: 'required_facts_present', required_facts: facts, missing_facts: []});
  assert.equal(present.label, '参数覆盖：所列参数均有依据');
  assert.deepEqual(plain(present.facts), facts);
  assert.match(present.explanation, /生成结论不在此检查范围/);
  for (const answer of [
    {answerability: 'not_assessed', required_facts: [], missing_facts: []},
    {answerability: 'not_assessed', required_facts: facts, missing_facts: []},
    {answerability: 'required_facts_present', required_facts: [], missing_facts: []},
    {},
  ]) {
    const status = ragFactCoverage(answer);
    assert.equal(status.label, '参数覆盖：未评估');
    assert.match(status.explanation, /不代表问题可完整回答/);
  }
  assert.equal(ragFactCoverage({answerability: 'required_facts_present', required_facts: facts, missing_facts: facts}).label, '参数覆盖：缺少必需依据');
});

test('多型号清单保留未知型号、缺失参数和标点，不把目录当作需求白名单', () => {
  const rows = [{product_model:' 未知 QL-22 ',parameter:'售价'}, {product_model:' 合成 QL-21 ',parameter:'额定压力 / 液体入口'}, {product_model:' ',parameter:''}];
  assert.deepEqual(plain(validateRagFactRows(rows)), [
    {product_model:'未知 QL-22',parameter:'售价'}, {product_model:'合成 QL-21',parameter:'额定压力 / 液体入口'},
  ]);
  assert.equal(rows[0].product_model, ' 未知 QL-22 ');
  assert.equal(validateRagFactRows([{product_model:'A',parameter:'长, 宽；温度'}])[0].parameter, '长, 宽；温度');
});

test('多行中半填不能被静默丢掉，超数量和超长仍拒绝', () => {
  const complete={product_model:'产品',parameter:'参数'};
  assert.throws(()=>validateRagFactRows([complete,{product_model:'另一个型号',parameter:''}]), /每一行/);
  assert.throws(()=>validateRagFactRows([complete,{product_model:'',parameter:'目录没有的参数'}]), /每一行/);
  assert.throws(()=>validateRagFactRows(Array.from({length:11},()=>complete)), /10/);
  assert.throws(()=>validateRagFactRows([{...complete,product_model:'甲'.repeat(201)}]), /200/);
  assert.deepEqual(plain(validateRagFactRows([{product_model:' ',parameter:' '}])), []);
});

test('主动选择目录条目补空行或追加，不覆盖已填写的未知型号和缺项', () => {
  const price={product_model:'未知型号',parameter:'售价'};
  const selected={product_model:'已知型号',parameter:'电压',evidence:[{chunk_id:1}]};
  const rows=[price,{product_model:'',parameter:''}];
  assert.deepEqual(plain(addRagCatalogFact(rows,selected)), [price,{product_model:'已知型号',parameter:'电压'}]);
  assert.deepEqual(plain(rows), [price,{product_model:'',parameter:''}]);
  assert.deepEqual(plain(addRagCatalogFact([price],selected)), [price,{product_model:'已知型号',parameter:'电压'}]);
});

test('目录去重保留单位大小写，满清单不截断原要求', () => {
  const row={product_model:'合成Ａ－１',parameter:'mA'};
  const rows=[row];
  assert.equal(addRagCatalogFact(rows,{product_model:' 合成 A-1 ',parameter:' mA '}), rows);
  assert.equal(addRagCatalogFact(rows,{product_model:'合成A-1',parameter:'MA'}).length, 2);
  assert.throws(()=>addRagCatalogFact(Array.from({length:10},(_,i)=>({product_model:'型号',parameter:`参数${i}`})),row), /10/);
});
