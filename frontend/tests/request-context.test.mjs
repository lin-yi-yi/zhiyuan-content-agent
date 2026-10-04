import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import test from 'node:test';

function harness(fetchImpl){
 const events=[];const output={};
 const code=ts.transpileModule(readFileSync(new URL('../src/api/client.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
 const context={exports:output,Headers,AbortController,DOMException,Event,window:{dispatchEvent:e=>events.push(e.type)},fetch:fetchImpl};vm.runInNewContext(code,context);
 return {...output,events};
}
function defer(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
const ok=(value)=>({ok:true,status:200,json:async()=>value});

test('组织与CSRF贯穿写请求，安全合并调用方头部',async()=>{
 let sent;const api=harness(async(url,opts)=>{sent={url,opts};return ok({saved:true});});
 api.configureRequestContext('team-a','csrf-a');await api.request('/api/evidence/notes',{method:'POST',headers:{'X-Request-ID':'one'},body:'{}'});
 assert.equal(sent.opts.credentials,'same-origin');assert.equal(sent.opts.headers.get('X-Organization-ID'),'team-a');assert.equal(sent.opts.headers.get('X-CSRF-Token'),'csrf-a');assert.equal(sent.opts.headers.get('X-Request-ID'),'one');
 await api.request('/api/knowledge/documents');assert.equal(sent.opts.headers.get('X-CSRF-Token'),null);
});
test('切换组织中止在途请求，忽略不服从中止的迟到响应',async()=>{
 const slow=defer();let signal;const api=harness(async(_url,opts)=>{signal=opts.signal;return slow.promise;});api.configureRequestContext('team-a','csrf');
 const result=api.request('/api/knowledge/documents');api.configureRequestContext('team-b','csrf');assert.equal(signal.aborted,true);slow.resolve(ok({organization:'team-a'}));await assert.rejects(result,e=>e.name==='AbortError');
});
test('JSON解析期间切换组织同样丢弃旧数据',async()=>{
 const slow=defer();const started=defer();const api=harness(async()=>({ok:true,status:200,json:()=>{started.resolve();return slow.promise;}}));api.configureRequestContext('team-a','csrf');const result=api.request('/api/evidence/notes');await started.promise;api.configureRequestContext('team-b','csrf');slow.resolve({items:['private-a']});await assert.rejects(result,e=>e.name==='AbortError');
});
test('清除会话后新请求不携带旧组织与CSRF，单个页面中止正常传递',async()=>{
 let sent;const api=harness(async(_url,opts)=>{sent=opts;return ok({});});api.configureRequestContext('team-a','csrf');api.configureRequestContext(null,null);const controller=new AbortController();controller.abort();await api.request('/api/saas/session',{signal:controller.signal});assert.equal(sent.signal.aborted,true);assert.equal(sent.headers.get('X-Organization-ID'),null);assert.equal(sent.headers.get('X-CSRF-Token'),null);
});

test('RAG 问答请求携带完整必需参数和知识库范围，缺项响应不会丢失', async()=>{
 const facts=[{product_model:'合成星桥 XP-24',parameter:'额定电压 / 直流输入'},{product_model:'合成星桥 XP-24',parameter:'售价'}];
 const response={answer:'缺少售价依据',refused:true,refusal_reason:'missing_structured_facts',answerability:'missing_required_facts',required_facts:facts,missing_facts:[facts[1]],coverage:{status:'sufficient'},citations:[]};
 let sent;const client=harness(async(url,opts)=>{sent={url,opts};return ok(response);});
 client.configureRequestContext('team-a','csrf-a');
 const result=await client.api.answerWithRag({query:'合成星桥 XP-24 电压和售价是多少？',workspace_id:7,knowledge_base_id:12,provider:'local',required_facts:facts});
 assert.equal(sent.url,'/api/v04/rag/answer');
 assert.equal(sent.opts.method,'POST');
 assert.equal(sent.opts.headers.get('X-Organization-ID'),'team-a');
 assert.equal(sent.opts.headers.get('X-CSRF-Token'),'csrf-a');
 const body=JSON.parse(sent.opts.body);
 assert.deepEqual(body.required_facts,facts);assert.equal(body.workspace_id,7);assert.equal(body.knowledge_base_id,12);
 assert.equal(result.refused,true);assert.deepEqual(result.missing_facts,[facts[1]]);
});
