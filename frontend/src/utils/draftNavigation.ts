import type { Card, Draft } from '../api/client';

export interface DraftNavigationTarget { draftId:number; topicId:number|null; runId:number }
interface DraftReader {
  getDraft:(id:number)=>Promise<Draft>;
  listCards:(id:number)=>Promise<Card[]>;
  listVersions:(topicId:number)=>Promise<Draft[]>;
}

/** Task links bind to an immutable ID, never to the latest version of a topic. */
export async function readTargetDraft(target:Pick<DraftNavigationTarget,'draftId'|'topicId'>,reader:DraftReader) {
  if(!Number.isSafeInteger(target.draftId)||target.draftId<1)throw new Error('任务没有有效的稿件编号。');
  const draft=await reader.getDraft(target.draftId);
  if(draft.id!==target.draftId)throw new Error('返回的稿件与任务指定版本不一致，已停止打开。');
  if(target.topicId!==null&&draft.topic_id!==target.topicId)throw new Error('稿件所属选题与任务不一致，已停止打开。');
  const [cards,versions]=await Promise.all([reader.listCards(draft.id),reader.listVersions(draft.topic_id)]);
  if(cards.some(card=>card.draft_id!==draft.id))throw new Error('卡片与指定稿件不一致，已停止打开。');
  return {draft,cards,versions:[draft,...versions.filter(item=>item.id!==draft.id&&item.topic_id===draft.topic_id)]};
}
