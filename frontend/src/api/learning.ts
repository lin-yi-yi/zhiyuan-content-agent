import { request } from './client';

export interface LearningQuestion { id:string; title:string; answer:string; followups:string[]; pitfall:string; source_ids:string[] }
export interface LearningLab { id:string; title:string; minutes:number; steps:{label:string;text:string}[]; source_ids:string[]; page:string }
export interface LearningModule { id:string; title:string; subtitle:string; order:number; technologies:string[]; summary:string; implemented:string; limitations:string; explanations:string[]; docs:{title:string;url:string}[]; questions:LearningQuestion[]; labs:LearningLab[] }
export interface LearningCatalog { version:string; title:string; notice:string; progress_notice:string; modules:LearningModule[]; sources:{id:string;path:string}[]; next_technologies:{name:string;status:string;reason:string;module:string}[] }
export interface SourceExcerpt { id:string; path:string; start_line:number; end_line:number; code:string; notice:string }

export const learningApi = {
  catalog:()=>request<LearningCatalog>('/api/learning/catalog'),
  source:(id:string)=>request<SourceExcerpt>(`/api/learning/source/${encodeURIComponent(id)}`),
};
