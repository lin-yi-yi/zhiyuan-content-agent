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
function defer(){let resolve,reject;const promise=new Promise((r,j)=>{resolve=r;reject=j;});return {promise,resolve,reject};}
const ok=(value)=>({ok:true,status:200,json:async()=>value});
const REQUEST_ID='0123456789abcdef0123456789abcdef';
const failed=(body,requestId=REQUEST_ID,status=422)=>({ok:false,status,headers:new Headers(requestId==null?{}:{'X-Request-ID':requestId}),text:async()=>body});

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
test('清除会话后新请求不携带旧组织与CSRF，已取消请求即使fetch返回成功也丢弃',async()=>{
 let sent;const api=harness(async(_url,opts)=>{sent=opts;return ok({});});api.configureRequestContext('team-a','csrf');api.configureRequestContext(null,null);const controller=new AbortController();controller.abort();await assert.rejects(api.request('/api/saas/session',{signal:controller.signal}),e=>e.name==='AbortError'&&!e.requestId);assert.equal(sent.signal.aborted,true);assert.equal(sent.headers.get('X-Organization-ID'),null);assert.equal(sent.headers.get('X-CSRF-Token'),null);
});

test('HTTP错误保持message并用类型化字段保存合法响应头编号',async()=>{
 const api=harness(async()=>failed(JSON.stringify({detail:'缺少有效资料',request_id:'正文伪造'})));
 await assert.rejects(api.request('/api/v04/rag/answer'),error=>{
  assert.ok(error instanceof api.RequestError);assert.equal(error.name,'RequestError');
  assert.equal(error.message,'缺少有效资料');assert.equal(error.status,422);assert.equal(error.requestId,REQUEST_ID);
  assert.ok(!error.message.includes(REQUEST_ID));return true;
 });
});

test('原有数组校验详情保留，正文中的编号不能覆盖响应头',async()=>{
 const api=harness(async()=>failed(JSON.stringify({detail:[{loc:['body','query'],msg:'字段不能为空'}],request_id:'f'.repeat(32)})));
 await assert.rejects(api.request('/api/v04/rag/answer'),error=>error.message==='query：字段不能为空'&&error.requestId===REQUEST_ID);
});

test('缺少或无效响应头时忽略正文和请求头伪造编号',async()=>{
 for(const value of [null,'A'.repeat(32),'a'.repeat(31),'a'.repeat(33),'a'.repeat(16)+'-'+ 'b'.repeat(15),'not-an-id']){
  const api=harness(async()=>failed(JSON.stringify({detail:'请求失败',request_id:REQUEST_ID}),value));
  await assert.rejects(api.request('/api/v04/rag/answer',{headers:{'X-Request-ID':REQUEST_ID}}),error=>{
   assert.ok(error instanceof api.RequestError);assert.equal(error.requestId,null);assert.equal(error.message,'请求失败');return true;
  });
 }
});

test('非JSON与空HTTP错误保留文本或状态回退，不遗失合法编号',async()=>{
 for(const [body,message] of [['服务暂时不可用','服务暂时不可用'],['','HTTP 503']]){
  const api=harness(async()=>failed(body,REQUEST_ID,503));
  await assert.rejects(api.request('/api/v04/rag/answer'),error=>error instanceof api.RequestError&&error.message===message&&error.requestId===REQUEST_ID);
 }
});

test('网络及正文读取失败不伪造HTTP排错编号',async()=>{
 const networkError=new TypeError('Failed to fetch');
 const api=harness(async()=>{throw networkError;});
 await assert.rejects(api.request('/api/v04/rag/answer'),error=>error===networkError&&!error.requestId);
 const bodyError=new TypeError('Body stream failed');
 const bodyApi=harness(async()=>({...failed(''),text:async()=>{throw bodyError;}}));
 await assert.rejects(bodyApi.request('/api/v04/rag/answer'),error=>error===bodyError&&!error.requestId);
});

test('错误正文读取期间切换组织丢弃旧错误信息和编号',async()=>{
 const slow=defer(),started=defer();
 const api=harness(async()=>({...failed(''),text:()=>{started.resolve();return slow.promise;}}));
 api.configureRequestContext('team-a','csrf-a');const result=api.request('/api/v04/rag/answer');
 await started.promise;api.configureRequestContext('team-b','csrf-b');
 slow.resolve(JSON.stringify({detail:'private-team-a-message',request_id:REQUEST_ID}));
 await assert.rejects(result,error=>error.name==='AbortError'&&!error.requestId&&!error.message.includes('private'));
});

test('错误正文读取失败与网络拒绝晚于组织切换时只返回取消',async()=>{
 for(const stage of ['body','fetch']){
  const slow=defer(),started=defer();
  const api=harness(stage==='body'?async()=>({...failed(''),text:()=>{started.resolve();return slow.promise;}}):async()=>{started.resolve();return slow.promise;});
  api.configureRequestContext('team-a','csrf-a');const result=api.request('/api/v04/rag/answer');await started.promise;
  api.configureRequestContext('team-b','csrf-b');slow.reject(new Error('private-team-a-body-failure'));
  await assert.rejects(result,error=>error.name==='AbortError'&&!error.requestId&&!error.message.includes('private'));
 }
});

test('单请求取消后，忽略不服从signal的错误正文与成功JSON',async()=>{
 for(const success of [true,false]){
  const slow=defer(),started=defer();const controller=new AbortController();
  const read=()=>{started.resolve();return slow.promise;};
  const api=harness(async()=>success?{ok:true,status:200,json:read}:{...failed(''),text:read});
  const result=api.request('/api/v04/rag/answer',{signal:controller.signal});await started.promise;controller.abort();
  slow.resolve(success?{private:'old response'}:JSON.stringify({detail:'old-error'}));
  await assert.rejects(result,error=>error.name==='AbortError'&&!error.requestId);
 }
});

test('复制工具只写合法编号；拒绝或无clipboard时返回手选结果',async()=>{
 const api=harness(async()=>ok({}));const output={};
 const code=ts.transpileModule(readFileSync(new URL('../src/components/RequestFailure.tsx',import.meta.url),'utf8'),{
  compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020,jsx:ts.JsxEmit.ReactJSX},
 }).outputText;
 vm.runInNewContext(code,{exports:output,require:path=>path==='../api/client'?api:{}});
 const values=[];assert.equal(await output.copyRequestId(REQUEST_ID,{writeText:async value=>values.push(value)}),true);
 assert.deepEqual(values,[REQUEST_ID]);
 assert.equal(await output.copyRequestId('not-a-request-id',{writeText:async()=>assert.fail('invalid id copied')}),false);
 assert.equal(await output.copyRequestId(REQUEST_ID,undefined),false);
 assert.equal(await output.copyRequestId(REQUEST_ID,{writeText:async()=>{throw new Error('permission denied');}}),false);
});

// Execute the real effect callbacks with controlled promises. This tests their
// request lifetimes without installing a DOM/React renderer or duplicating logic.
function ragInitializationHarness(initialKnowledgeBaseId){
 const source=readFileSync(new URL('../src/pages/RagLabPage.tsx',import.meta.url),'utf8');
 const parsed=ts.createSourceFile('RagLabPage.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
 const effects=[];let select;
 const visit=node=>{
  if(ts.isCallExpression(node)&&node.expression.getText(parsed)==='useEffect')effects.push(node.arguments[0].getText(parsed));
  if(ts.isVariableDeclaration(node)&&node.name.getText(parsed)==='setKnowledgeBaseId')select=node.initializer.getText(parsed);
  ts.forEachChild(node,visit);
 };
 visit(parsed);
 const initial=effects.find(text=>text.includes('api.listKnowledgeBases'));
 const invalidate=effects.find(text=>text.includes('setHits([])')&&text.includes('retrievalGeneration'));
 assert.ok(initial&&invalidate&&select,'RagLab initialization, question effects and selection callback must be identifiable');
 const output={},kb=defer(),status=defer();
 let initialError='',queryError='',selectionWarning='',knowledgeBases=null,selected=null;
 const code=ts.transpileModule(`exports.initialize=${initial};exports.invalidate=${invalidate};exports.select=${select};`,{
  compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020},
 }).outputText;
 const context={
  exports:output,retrievalGeneration:{current:0},initialScope:{workspace_id:7},initialKnowledgeBaseId,
  knowledgeBaseId:null,knowledgeBases:[],
  api:{listKnowledgeBases:()=>kb.promise},workbench:{status:()=>status.promise},
  setInitializationError:error=>{initialError=error;},setError:error=>{queryError=error;},
  setSelectionWarning:message=>{selectionWarning=message;},setFactRows:()=>{},setFactsConfirmed:()=>{},
  setKnowledgeBases:items=>{knowledgeBases=items;context.knowledgeBases=items;},setKnowledgeBaseId:id=>output.select(id),
  updateKnowledgeBaseId:id=>{selected=id;context.knowledgeBaseId=id;output.invalidate();},
  setStatusLoading:()=>{},setStatus:()=>{},setLoading:()=>{},setHits:()=>{},setAnswer:()=>{},setResultContext:()=>{},
 };
 vm.runInNewContext(code,context);
 return {...output,kb,status,view:()=>({initialError,queryError,selectionWarning,knowledgeBases,selected})};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));

test('知识库初始化晚于问题或模型变化失败时，错误编号仍显示且不会被后续编辑清除',async()=>{
 const setup=ragInitializationHarness(),api=harness(async()=>ok({}));
 const failure=new api.RequestError('知识库载入失败',500,REQUEST_ID);
 setup.initialize();setup.invalidate();setup.status.resolve({mode:'lexical'});setup.kb.reject(failure);await flush();
 assert.equal(setup.view().initialError,failure);assert.equal(setup.view().initialError.requestId,REQUEST_ID);
 assert.equal(setup.view().knowledgeBases.length,0);
 setup.invalidate();assert.equal(setup.view().initialError,failure);assert.equal(setup.view().queryError,'');
});

test('状态初始化先失败后知识库成功，选库的检索清理不抹掉初始化错误',async()=>{
 const setup=ragInitializationHarness(),api=harness(async()=>ok({}));
 const failure=new api.RequestError('检索配置读取失败',500,REQUEST_ID);
 setup.initialize();setup.status.reject(failure);await flush();
 assert.equal(setup.view().initialError,failure);
 setup.kb.resolve([{id:11,name:'合成库'}]);await flush();
 assert.equal(setup.view().selected,11);assert.equal(setup.view().initialError,failure);
});

test('无效预选知识库的提示不被输入或默认选库清理吞掉',async()=>{
 const setup=ragInitializationHarness(99);
 setup.initialize();setup.status.resolve({mode:'lexical'});setup.kb.resolve([{id:11,name:'合成库'}]);await flush();
 assert.equal(setup.view().selected,null);assert.match(setup.view().selectionWarning,/指定知识库不可用/);
 setup.invalidate();assert.match(setup.view().selectionWarning,/指定知识库不可用/);
 setup.select(11);assert.equal(setup.view().selectionWarning,'');
});

test('手选有效库只消除无效预选提示，保留真实状态加载错误与编号',async()=>{
 const setup=ragInitializationHarness(99),api=harness(async()=>ok({}));
 const failure=new api.RequestError('状态加载失败',500,REQUEST_ID);
 setup.initialize();setup.status.reject(failure);setup.kb.resolve([{id:11,name:'合成库'}]);await flush();
 assert.equal(setup.view().initialError,failure);assert.match(setup.view().selectionWarning,/指定知识库不可用/);
 setup.select(null);assert.match(setup.view().selectionWarning,/指定知识库不可用/);
 setup.select(11);assert.equal(setup.view().selectionWarning,'');assert.equal(setup.view().selected,11);
 assert.equal(setup.view().initialError,failure);assert.equal(setup.view().initialError.requestId,REQUEST_ID);
});

test('组织工作区切换或卸载后的初始化回调不泄漏旧错误和知识库',async()=>{
 for(const fail of [true,false]){
  const setup=ragInitializationHarness(),api=harness(async()=>ok({}));
  const cleanup=setup.initialize();cleanup();
  setup.status.reject(new api.RequestError('旧组织状态',500,REQUEST_ID));
  if(fail)setup.kb.reject(new api.RequestError('旧组织知识库',500,REQUEST_ID));
  else setup.kb.resolve([{id:11,name:'旧组织知识库'}]);
  await flush();assert.equal(setup.view().initialError,'');assert.equal(setup.view().knowledgeBases,null);
 }
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
