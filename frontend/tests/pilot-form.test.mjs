import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import test from 'node:test';

const exported = {};
const source = readFileSync(new URL('../src/utils/pilotForm.ts', import.meta.url), 'utf8');
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020},
}).outputText, {exports: exported});
const {recordToForm, formToInput} = exported;

test('未采集工时保留 null，显式 0 支持工时可往返', () => {
  const form = recordToForm(null);
  assert.equal(formToInput(form).actual_work_minutes, null);
  assert.equal(formToInput({...form, support_minutes: '0'}).support_minutes, 0);
  assert.equal(formToInput({...form, support_minutes: '  '}).support_minutes, null);
  const restored = recordToForm({version: 3, cohort: 'A', outcome: 'pending', actual_work_minutes: 0, support_minutes: null});
  assert.equal(restored.actual_work_minutes, '0');
  assert.equal(restored.support_minutes, '');
  assert.equal(formToInput(restored).version, 3);
});

test('零基线、负值及非有限工时不能成为节省依据', () => {
  const form = recordToForm(null);
  for (const value of ['0', '0.001', '1e-308', '-1', 'Infinity', 'NaN', '100001', '1e308']) {
    assert.throws(() => formToInput({...form, baseline_minutes: value, baseline_reference: '对照'}));
  }
  for (const field of ['actual_work_minutes', 'support_minutes']) {
    for (const value of ['-1', 'Infinity', 'NaN', '100001']) assert.throws(() => formToInput({...form, [field]: value}));
  }
});

test('验收与对照必须有人可回查的依据，不能由内部审核状态自动填充', () => {
  const form = recordToForm(null);
  assert.throws(() => formToInput({...form, outcome: 'accepted'}));
  assert.throws(() => formToInput({...form, baseline_minutes: '30', baseline_reference: '   '}));
  const input = formToInput({...form, version: 4, outcome: 'accepted', acceptance_reference: ' 客户任务 C-7 ', baseline_minutes: '30', baseline_reference: ' 历史同类任务 ', actual_work_minutes: '40', support_minutes: '5'});
  assert.equal(input.version, 4);
  assert.equal(input.actual_work_minutes, 40);
  assert.equal(input.acceptance_reference, '客户任务 C-7');
  assert.equal(input.baseline_reference, '历史同类任务');
});

test('失败与放弃任务无需虚构基线或验收数据', () => {
  const input = formToInput({...recordToForm(null), outcome: 'abandoned', note: '资料不足'});
  assert.equal(input.baseline_minutes, null);
  assert.equal(input.acceptance_reference, '');
  assert.equal(input.outcome, 'abandoned');
  assert.throws(() => formToInput({...recordToForm(null), cohort: ' '}));
});
