import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import test from 'node:test';
const output={};
const code=ts.transpileModule(readFileSync(new URL('../src/utils/draftNavigation.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
vm.runInNewContext(code,{exports:output});
const {readTargetDraft}=output;

test('任务指定旧版时仍打开旧稿，最新版本只列为可选项',async()=>{
 const calls=[];
 const result=await readTargetDraft({draftId:7,topicId:3},{
  getDraft:async id=>{calls.push(['get',id]);return {id,topic_id:3,status:'rejected'};},
  listCards:async id=>{calls.push(['cards',id]);return [{id:21,draft_id:id}];},
  listVersions:async id=>{calls.push(['versions',id]);return [{id:9,topic_id:3},{id:8,topic_id:3}];},
 });
 assert.equal(result.draft.id,7);assert.equal(result.draft.status,'rejected');assert.equal(result.cards[0].draft_id,7);
 assert.deepEqual(Array.from(result.versions,item=>item.id),[7,9,8]);assert.deepEqual(calls,[['get',7],['cards',7],['versions',3]]);
});
test('指定稿件不存在时返回错误，不读取同题最新版本作为替代',async()=>{
 let downstreamReads=0;
 await assert.rejects(readTargetDraft({draftId:404,topicId:3},{getDraft:async()=>{throw new Error('草稿不存在');},listCards:async()=>{downstreamReads++;return [];},listVersions:async()=>{downstreamReads++;return [{id:9,topic_id:3}];}}),/草稿不存在/);
 assert.equal(downstreamReads,0);
});
test('拒绝错配稿件或选题，防止错误版本进入编辑状态',async()=>{
 for(const draft of [{id:8,topic_id:3},{id:7,topic_id:4}]){
  let reads=0;await assert.rejects(readTargetDraft({draftId:7,topicId:3},{getDraft:async()=>draft,listCards:async()=>{reads++;return [];},listVersions:async()=>{reads++;return [];}}),/不一致/);assert.equal(reads,0);
 }
});
test('缺少选题编号时以精确稿件的选题为准，卡片也必须归属该稿件',async()=>{
 let requestedTopic;
 const reader={getDraft:async()=>({id:7,topic_id:3}),listCards:async()=>[],listVersions:async id=>{requestedTopic=id;return [];}};
 const result=await readTargetDraft({draftId:7,topicId:null},reader);assert.equal(requestedTopic,3);assert.equal(result.versions[0].id,7);
 await assert.rejects(readTargetDraft({draftId:7,topicId:null},{...reader,listCards:async()=>[{id:8,draft_id:999}]}),/卡片与指定稿件不一致/);
});
