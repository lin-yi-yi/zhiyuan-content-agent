import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import test from 'node:test';

const output={};
const code=ts.transpileModule(readFileSync(new URL('../src/utils/learningProgress.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
vm.runInNewContext(code,{exports:output});
const {learningStorageKey,emptyLearningProgress,parseLearningProgress,updateLearningRating,updateLearningLab,updateLearningAnswer}=output;

test('学习进度按模式、账号、组织分别隔离，分隔符不造成键碰撞',()=>{
  const identities=[{mode:'local'},{mode:'saas',userId:'u1',organizationId:'o1'},{mode:'saas',userId:'u2',organizationId:'o1'},{mode:'saas',userId:'u1',organizationId:'o2'},{mode:'saas'},{mode:'saas',userId:'u:a',organizationId:'b'},{mode:'saas',userId:'u',organizationId:'a:b'}];
  assert.equal(new Set(identities.map(learningStorageKey)).size,identities.length);
  assert.equal(learningStorageKey({mode:'local',userId:'ignored',organizationId:'ignored'}),learningStorageKey({mode:'local'}));
});

test('刷新后恢复自测、练习、自己的答案，但不会从其他键读进度',()=>{
  let p=emptyLearningProgress();p=updateLearningRating(p,'rag-citation','explain');p=updateLearningLab(p,'lab-rag-citation',true);p=updateLearningAnswer(p,'rag-citation','引用存在不等于结论正确');
  const store=new Map([[learningStorageKey({mode:'saas',userId:'u1',organizationId:'a'}),JSON.stringify(p)]]);
  const restored=parseLearningProgress(store.get(learningStorageKey({mode:'saas',userId:'u1',organizationId:'a'})));
  const other=parseLearningProgress(store.get(learningStorageKey({mode:'saas',userId:'u1',organizationId:'b'})));
  assert.equal(restored.ratings['rag-citation'],'explain');assert.equal(restored.labs['lab-rag-citation'],true);assert.equal(restored.answers['rag-citation'],'引用存在不等于结论正确');assert.ok(restored.updatedAt);
  assert.equal(Object.keys(other.ratings).length,0);assert.equal(Object.keys(other.answers).length,0);
});

test('损坏或异常缓存安全回退，不采纳非法掌握程度或伪造布尔值',()=>{
  for(const raw of ['broken','null','[]','{"version":2}', 'x'.repeat(150001)])assert.equal(Object.keys(parseLearningProgress(raw).ratings).length,0);
  const parsed=parseLearningProgress(JSON.stringify({version:1,ratings:{'rag-good':'explain','rag-bad':'certified','__proto__':'explain'},labs:{'lab-good':true,'lab-bad':'true'},answers:{'rag-note':'x'.repeat(3000)},updatedAt:'bad-date'}));
  assert.equal(parsed.ratings['rag-good'],'explain');assert.equal(parsed.ratings['rag-bad'],undefined);assert.equal(parsed.labs['lab-bad'],undefined);assert.equal(parsed.answers['rag-note'].length,2000);assert.equal(parsed.updatedAt,null);
});

test('更新不可变，不会自动把看过答案或完成练习升级为掌握',()=>{
  const original=emptyLearningProgress();const rated=updateLearningRating(original,'rag-citation','learning');const done=updateLearningLab(rated,'lab-rag-citation',true);
  assert.equal(original.ratings['rag-citation'],undefined);assert.equal(rated.labs['lab-rag-citation'],undefined);assert.equal(done.ratings['rag-citation'],'learning');
  assert.equal(updateLearningRating(original,'../secret','explain'),original);assert.equal(updateLearningRating(original,'rag-citation','expert'),original);
});
