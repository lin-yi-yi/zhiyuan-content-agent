import { request } from './client';

export interface BrandProfile {
  id:number; name:string; audience:string; tone:string; prohibited_claims:string;
  call_to_action:string; knowledge_base_id:number; workspace_id:number;
  data_policy:'cloud_allowed'|'local_only'; is_active:boolean; version:number;
  created_at:string; updated_at:string;
}
export type BrandInput = Omit<BrandProfile,'id'|'created_at'|'updated_at'|'version'|'workspace_id'> & {workspace_id?:number;version?:number};
export interface ContentWorkflow {key:'knowledge_post'|'product_faq'|'case_story';name:string;description:string;required_materials:string[];instructions:string}
export interface BusinessBrief {profile:BrandProfile|null;workflow:ContentWorkflow;knowledge_base_id:number;delivery:string}
function writable(body:BrandInput){
  const {name,audience,tone,prohibited_claims,call_to_action,knowledge_base_id,workspace_id,data_policy,is_active,version}=body;
  return {name,audience,tone,prohibited_claims,call_to_action,knowledge_base_id,workspace_id,data_policy,is_active,...(version!==undefined?{version}:{})};
}
export const brandsApi = {
  list:()=>request<BrandProfile[]>('/api/brands'),
  create:(body:BrandInput)=>request<BrandProfile>('/api/brands',{method:'POST',body:JSON.stringify(writable(body))}),
  update:(id:number,body:BrandInput)=>request<BrandProfile>(`/api/brands/${id}`,{method:'PUT',body:JSON.stringify(writable(body))}),
  workflows:()=>request<ContentWorkflow[]>('/api/brands/workflows'),
  delivery:(id:number)=>request<{markdown:string;title:string}>(`/api/agent-runs/${id}/delivery`),
};
