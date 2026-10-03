export type Mastery = 'new'|'learning'|'explain'|'demonstrate';
export interface LearningProgress { version:1; ratings:Record<string,Mastery>; labs:Record<string,boolean>; answers:Record<string,string>; updatedAt:string|null }
export interface LearningIdentity { mode:'local'|'saas'; userId?:string|null; organizationId?:string|null }
const LEVELS:Mastery[] = ['new','learning','explain','demonstrate'];
const ID = /^[a-z][a-z0-9-]{1,80}$/;

// JSON encoding preserves boundaries: a colon inside an ID cannot collide.
export function learningStorageKey(identity:LearningIdentity):string {
  const scope = identity.mode==='local' ? ['local','single-user','local'] : ['saas',identity.userId || 'signed-out',identity.organizationId || 'no-organization'];
  return `learning-progress:v1:${JSON.stringify(scope)}`;
}

export function emptyLearningProgress():LearningProgress {
  return {version:1,ratings:{},labs:{},answers:{},updatedAt:null};
}

export function parseLearningProgress(raw:string|null):LearningProgress {
  const clean=emptyLearningProgress();
  if(!raw || raw.length>150000)return clean;
  try {
    const value=JSON.parse(raw);
    if(!value || value.version!==1)return clean;
    for(const [id,level] of Object.entries(value.ratings || {})) {
      if(ID.test(id) && LEVELS.includes(level as Mastery))clean.ratings[id]=level as Mastery;
    }
    for(const [id,done] of Object.entries(value.labs || {})) {
      if(ID.test(id) && typeof done==='boolean')clean.labs[id]=done;
    }
    for(const [id,answer] of Object.entries(value.answers || {})) {
      if(ID.test(id) && typeof answer==='string')clean.answers[id]=answer.slice(0,2000);
    }
    clean.updatedAt=typeof value.updatedAt==='string' && !Number.isNaN(Date.parse(value.updatedAt)) ? value.updatedAt : null;
    return clean;
  }catch{return clean;}
}

export function updateLearningRating(progress:LearningProgress,id:string,level:Mastery):LearningProgress {
  if(!ID.test(id)||!LEVELS.includes(level))return progress;
  return {...progress,ratings:{...progress.ratings,[id]:level},updatedAt:new Date().toISOString()};
}

export function updateLearningLab(progress:LearningProgress,id:string,done:boolean):LearningProgress {
  if(!ID.test(id))return progress;
  return {...progress,labs:{...progress.labs,[id]:done},updatedAt:new Date().toISOString()};
}

export function updateLearningAnswer(progress:LearningProgress,id:string,answer:string):LearningProgress {
  if(!ID.test(id))return progress;
  return {...progress,answers:{...progress.answers,[id]:answer.slice(0,2000)},updatedAt:new Date().toISOString()};
}
