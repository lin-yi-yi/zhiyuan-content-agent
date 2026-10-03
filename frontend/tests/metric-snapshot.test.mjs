import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import test from 'node:test';
const output={};
const code=ts.transpileModule(readFileSync(new URL('../src/utils/metricSnapshot.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
vm.runInNewContext(code,{exports:output});
const {latestMetricSnapshot}=output;
test('累计快照只取最新采集值，不相加且不依赖API返回顺序',()=>{
 const data=[{id:9,collected_at:'2026-09-19T03:00:00Z',views:150},{id:12,collected_at:'2026-09-18T03:00:00Z',views:100}];
 assert.equal(latestMetricSnapshot(data).views,150);assert.equal(latestMetricSnapshot([...data].reverse()).id,9);assert.equal(data[0].id,9);
});
test('同一采集时刻以较大的快照编号为准，包括时区不同但表示同一时刻',()=>{
 const data=[{id:2,collected_at:'2026-09-19T11:00:00+08:00',views:100},{id:3,collected_at:'2026-09-19T03:00:00Z',views:120}];assert.equal(latestMetricSnapshot(data).id,3);
});
test('没有快照返回null，明确录入的最新零值仍有效',()=>{
 assert.equal(latestMetricSnapshot([]),null);const data=[{id:1,collected_at:'2026-09-18T00:00:00Z',views:10},{id:2,collected_at:'2026-09-19T00:00:00Z',views:0}];assert.equal(latestMetricSnapshot(data).views,0);
});
test('日期异常的历史行不能盖过有有效采集时间的记录',()=>{
 assert.equal(latestMetricSnapshot([{id:999,collected_at:'invalid'},{id:1,collected_at:'2026-09-19T00:00:00Z'}]).id,1);
});
