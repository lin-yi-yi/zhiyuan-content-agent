import { useEffect, useRef, useState } from 'react';
import AppShell from './components/AppShell';
import SourceLibraryPage from './pages/SourceLibraryPage';
import TopicPoolPage from './pages/TopicPoolPage';
import DraftEditorPage from './pages/DraftEditorPage';
import MetricsPage from './pages/MetricsPage';
import ReportsPage from './pages/ReportsPage';
import PilotPage from './pages/PilotPage';
import SettingsPage from './pages/SettingsPage';
import AgentWorkbenchPage from './pages/AgentWorkbenchPage';
import RagLabPage from './pages/RagLabPage';
import ErrorBoundary from './components/ErrorBoundary';
import KnowledgePage from './pages/KnowledgePage';
import SourceHubPage from './pages/SourceHubPage';
import EvidencePage from './pages/EvidencePage';
import AuthPage from './pages/AuthPage';
import BusinessHomePage from './pages/BusinessHomePage';
import BrandsPage from './pages/BrandsPage';
import ConnectionsPage from './pages/ConnectionsPage';
import TeamPage from './pages/TeamPage';
import BillingPage from './pages/BillingPage';
import AccountPage from './pages/AccountPage';
import { EvidenceScope, EvidenceSeed } from './api/evidence';
import { SaasSession, saasApi } from './api/saas';
import { configureRequestContext } from './api/client';
import { ReadOnlyPage, WorkspaceContext } from './components/WorkspaceContext';
import {parseWorkbenchRoute,routeHash,routeNumber} from './utils/navigation';
function savedOrganization():string|null {try{return sessionStorage.getItem('zhiyuan-active-organization');}catch{return null;}}
function rememberOrganization(id:string|null){try{if(id)sessionStorage.setItem('zhiyuan-active-organization',id);else sessionStorage.removeItem('zhiyuan-active-organization');}catch{/* Storage can be disabled; server membership checks remain authoritative. */}}
function WorkspacePages(){
 const initialRoute=()=>parseWorkbenchRoute(window.location.hash||(new URLSearchParams(window.location.search).has('invite')?'account':'overview'));
 const [route,setRoute]=useState(initialRoute);
 const [evidenceSeed,setEvidenceSeed]=useState<EvidenceSeed|null>(null);const [evidenceNoteId,setEvidenceNoteId]=useState<number|null>(null);const [evidenceScope,setEvidenceScope]=useState<EvidenceScope|undefined>();
 useEffect(()=>{const sync=()=>setRoute(initialRoute());window.addEventListener('hashchange',sync);return()=>window.removeEventListener('hashchange',sync);},[]);
 const navigate=(next:string)=>{const value=parseWorkbenchRoute(next);if(value.page==='evidence'){setEvidenceScope(undefined);setEvidenceNoteId(null);setEvidenceSeed(null);}setRoute(value);window.location.hash=routeHash(value);window.scrollTo({top:0,behavior:'instant'});};
 const page=route.page, kb=routeNumber(route,'kb'), runId=routeNumber(route,'run'), draftId=routeNumber(route,'draft'), topicId=routeNumber(route,'topic');
 const draftTarget=draftId&&runId?{draftId,topicId,runId}:null;
 const useKnowledge=(next:'rag'|'agent',id:number)=>navigate(`${next}?kb=${id}`);
 const openDraft=(id:number,topic:number|null,run:number)=>navigate(`drafts?draft=${id}&run=${run}${topic?`&topic=${topic}`:''}`);
 const returnToAgent=(id:number)=>navigate(`agent?run=${id}`);
 const openEvidence=(id:number,scope?:EvidenceScope)=>{navigate('evidence');setEvidenceScope(scope);setEvidenceSeed(null);setEvidenceNoteId(id);};
 const createEvidence=(seed:EvidenceSeed)=>{navigate('evidence');setEvidenceScope(undefined);setEvidenceNoteId(null);setEvidenceSeed(seed);};
 return <AppShell active={page} onNavigate={navigate}><ErrorBoundary key={`${page}-${kb??''}-${runId??''}`}>
 {page==='overview'&&<BusinessHomePage onNavigate={navigate}/>}
 {page==='brands'&&<BrandsPage key={routeNumber(route,'brand')??'new'} initialBrandId={routeNumber(route,'brand')} onNavigate={navigate}/>}
 {page==='knowledge'&&<KnowledgePage key={kb??'default'} initialKnowledgeBaseId={kb??undefined} onOpenEvidence={openEvidence} onNavigate={navigate} onUseKnowledgeBase={useKnowledge}/>}
 {page==='source-hub'&&<SourceHubPage onCreateEvidence={createEvidence} onNavigate={navigate}/>}
 {page==='evidence'&&<EvidencePage key={`${evidenceScope?.workspace_id??'default'}-${evidenceScope?.knowledge_base_id??'default'}-${evidenceNoteId??'new'}`} scope={evidenceScope} seed={evidenceSeed} initialNoteId={evidenceNoteId} onSeedConsumed={()=>setEvidenceSeed(null)} onNavigate={navigate}/>}
 {page==='agent'&&<AgentWorkbenchPage key={`${runId??'manual'}-${kb??'default'}-${routeNumber(route,'brand')??'none'}-${route.params.get('workflow')||'default'}`} initialBrandId={routeNumber(route,'brand')} initialWorkflow={route.params.get('workflow')} initialRunId={runId} initialKnowledgeBaseId={kb??undefined} onOpenDraft={openDraft} onOpenEvidence={openEvidence} onNavigate={navigate}/>}
 {page==='sources'&&<ReadOnlyPage><SourceLibraryPage/></ReadOnlyPage>}
 {page==='topics'&&<ReadOnlyPage><TopicPoolPage/></ReadOnlyPage>}
 {page==='drafts'&&<ReadOnlyPage><DraftEditorPage key={draftTarget?`${draftTarget.runId}-${draftTarget.draftId}`:'manual'} target={draftTarget} onReturnToAgent={returnToAgent} onNavigate={navigate}/></ReadOnlyPage>}
 {page==='metrics'&&<ReadOnlyPage><MetricsPage/></ReadOnlyPage>}
 {page==='reports'&&<ReadOnlyPage><ReportsPage/></ReadOnlyPage>}
 {page==='pilot'&&<ReadOnlyPage><PilotPage onNavigate={navigate}/></ReadOnlyPage>}
 {page==='settings'&&<ReadOnlyPage><SettingsPage/></ReadOnlyPage>}
 {page==='rag'&&<RagLabPage key={kb??'default'} initialKnowledgeBaseId={kb??undefined} onOpenEvidence={openEvidence} onNavigate={navigate}/>}
 {page==='connections'&&<ConnectionsPage onNavigate={navigate}/>}
 {page==='team'&&<TeamPage/>}
 {page==='billing'&&<BillingPage/>}
 {page==='account'&&<AccountPage onNavigate={navigate}/>}
 </ErrorBoundary></AppShell>;
}
export default function App(){
 const [session,setSession]=useState<SaasSession|null>(null);const [organizationId,setOrganizationId]=useState<string|null>(null);const organizationRef=useRef<string|null>(savedOrganization());const csrfRef=useRef<string|null>(null);const [error,setError]=useState('');
 const applySession=(value:SaasSession)=>{const id=value.mode==='saas'&&value.authenticated?(value.organizations.find(item=>String(item.id)===organizationRef.current)?.id||value.active_organization_id||value.organizations[0]?.id||null):null;const normalized=id===null?null:String(id);organizationRef.current=normalized;rememberOrganization(normalized);csrfRef.current=value.csrf_token;configureRequestContext(normalized,value.csrf_token);setOrganizationId(normalized);setSession(value);setError('');};
 const refreshSession=async()=>{applySession(await saasApi.session());};
 const load=()=>{setError('');void refreshSession().catch(e=>setError(e instanceof Error?e.message:'暂时无法连接工作空间'));};
 useEffect(()=>{load();const expired=()=>{configureRequestContext(null,null);organizationRef.current=null;rememberOrganization(null);csrfRef.current=null;setSession(old=>old?{...old,authenticated:false,user:null,organizations:[],csrf_token:null}:null);};window.addEventListener('saas-session-expired',expired);return()=>window.removeEventListener('saas-session-expired',expired);},[]);
 const switchOrganization=(id:string)=>{window.location.hash='#overview';organizationRef.current=String(id);rememberOrganization(String(id));configureRequestContext(String(id),csrfRef.current);setOrganizationId(String(id));};
 const logout=async()=>{await saasApi.logout();configureRequestContext(null,null);organizationRef.current=null;rememberOrganization(null);csrfRef.current=null;setOrganizationId(null);setSession(old=>old?{...old,authenticated:false,user:null,organizations:[],active_organization_id:null,csrf_token:null}:null);await refreshSession();};
 if(!session)return <div className="workspace-loading"><span className="brand-mark">知</span><h1>知源</h1><p>{error||'正在打开工作空间…'}</p>{error&&<button className="btn btn-primary" onClick={load}>重新连接</button>}</div>;
 if(session.mode==='saas'&&!session.authenticated)return <AuthPage session={session} onAuthenticated={applySession}/>;
 const organization=session.organizations.find(item=>String(item.id)===organizationId)||null;const local=session.mode==='local';const owner=local||organization?.role==='owner';
 return <WorkspaceContext.Provider value={{session,organization,canWrite:owner||organization?.role==='editor',canReview:owner||organization?.role==='reviewer',canManage:owner,isOwner:owner,refreshSession,switchOrganization,logout}}><WorkspacePages key={`${session.mode}-${session.user?.id||'local'}-${organizationId||'local'}`}/></WorkspaceContext.Provider>;
}
