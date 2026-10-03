import { useEffect, useState } from 'react';
import { request } from '../api/client';
interface AvailableModel {provider:string;model:string;configured:boolean;enabled?:boolean;effective_enabled?:boolean;is_default?:boolean}
export function useAvailableModels(){const [models,setModels]=useState<AvailableModel[]>([]);const [modelsError,setError]=useState('');useEffect(()=>{let active=true;request<{providers:AvailableModel[]}>('/api/models/providers').then(data=>{if(active)setModels(data.providers.filter(item=>item.effective_enabled??(item.enabled!==false&&item.configured)));}).catch(e=>{if(active)setError(e instanceof Error?e.message:'无法读取可用模型');});return()=>{active=false;};},[]);return {models,modelsError};}
