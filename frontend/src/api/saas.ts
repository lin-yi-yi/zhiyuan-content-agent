import { request } from './client';

export type TeamRole = 'owner'|'editor'|'reviewer'|'viewer';
export interface Organization { id:string; name:string; role:TeamRole }
export interface SaasSession { mode:'local'|'saas'; authenticated:boolean; user:{id:string;email:string;name:string}|null; organizations:Organization[]; active_organization_id:string|null; csrf_token:string|null; allow_registration:boolean }
export interface TeamMember { id:string;user_id:string;email:string;name:string;role:TeamRole;is_active:boolean }
export interface TeamInvite { id:string;email:string;role:TeamRole;expires_at:string;token:string;invite_url:string }
export interface Connection {provider:string;name:string;kind:'source'|'import'|'model'|'integration';supported:boolean;enabled:boolean;effective_enabled:boolean;configured:boolean;status:string;can_toggle:boolean;is_default:boolean;credential_status:string;credential_source:string|null;description:string}
export interface Connections { connections:Connection[];default_model_provider:string }
export interface Billing {plan:{code:string;name:string};limits:{ai_requests:number;documents:number;members:number};usage:{ai_requests:{used:number;reserved:number;settled:number;remaining:number;attempts:number;refunded:number};documents:{used:number|null;limit:number;measured:boolean};members:{used:number;limit:number}};period:{start:string;end:string;label:string};upgrade_requests:Array<{id:string;requested_plan:string;status:string;created_at:string}>;usage_events:Array<{request_ref:string;metric:string;amount:number;status:string;created_at:string;completed_at:string|null}>;notice:string}
const post = (body:unknown) => ({method:'POST',body:JSON.stringify(body)});
export const saasApi = {
  session:()=>request<SaasSession>('/api/saas/session'),
  login:(email:string,password:string)=>request<SaasSession>('/api/saas/auth/login',post({email,password})),
  register:(body:{name:string;email:string;password:string;organization_name:string;invite_token?:string})=>request<SaasSession>('/api/saas/auth/register',post(body)),
  logout:()=>request<{logged_out:boolean}>('/api/saas/auth/logout',post({})),
  organizations:()=>request<{items:Organization[]}>('/api/saas/organizations'),
  createOrganization:(name:string)=>request<Organization>('/api/saas/organizations',post({name})),
  members:()=>request<{items:TeamMember[]}>('/api/saas/members'),
  updateMember:(id:string,body:{role?:TeamRole;is_active?:boolean})=>request<TeamMember>(`/api/saas/members/${id}`,{method:'PATCH',body:JSON.stringify(body)}),
  invite:(email:string,role:TeamRole)=>request<TeamInvite>('/api/saas/invites',post({email,role})),
  acceptInvite:(token:string)=>request<{organization:Organization}>('/api/saas/invites/accept',post({token})),
  connections:()=>request<Connections>('/api/saas/connections'),
  testConnection:(provider:string)=>request<{ok:boolean;response:string}>(`/api/models/test/${encodeURIComponent(provider)}`,post({})),
  updateConnection:(provider:string,body:{enabled?:boolean;is_default?:boolean})=>request<Connections>(`/api/saas/connections/${encodeURIComponent(provider)}`,{method:'PATCH',body:JSON.stringify(body)}),
  billing:()=>request<Billing>('/api/saas/billing'),
  upgrade:(note:string)=>request<{id:string;requested_plan:string;status:string;created_at:string}>('/api/saas/billing/upgrade-request',post({requested_plan:'team',note})),
};
export const ROLE_LABELS:Record<TeamRole,string>={owner:'组织所有者',editor:'内容编辑',reviewer:'审核人员',viewer:'只读成员'};
