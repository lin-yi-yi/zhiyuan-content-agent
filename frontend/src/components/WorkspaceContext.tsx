import { createContext, useContext } from 'react';
import { Organization, SaasSession } from '../api/saas';
export interface WorkspaceAccess { session:SaasSession; organization:Organization|null; canWrite:boolean; canReview:boolean; canManage:boolean; isOwner:boolean; refreshSession:()=>Promise<void>; switchOrganization:(id:string)=>void; logout:()=>Promise<void> }
export const WorkspaceContext=createContext<WorkspaceAccess|null>(null);
export function useWorkspace(){const value=useContext(WorkspaceContext);if(!value)throw new Error('工作区尚未初始化');return value;}
export function ReadOnlyPage({children}:{children:React.ReactNode}) {const {canWrite}=useWorkspace();return <div className="readonly-page">{!canWrite&&<div className="notice-strip">当前角色可以查看已有内容。修改与生成需要内容编辑或组织所有者权限。</div>}{children}</div>;}
