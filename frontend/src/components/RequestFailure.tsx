import {useEffect, useRef, useState} from 'react';
import {RequestError, validRequestId} from '../api/client';
import '../styles/requestFailure.css';

export async function copyRequestId(requestId: string, clipboard?: Pick<Clipboard, 'writeText'>): Promise<boolean> {
  if (!validRequestId(requestId) || !clipboard) return false;
  try {
    await clipboard.writeText(requestId);
    return true;
  } catch {
    return false;
  }
}

export default function RequestFailure({error}: {error: Error | string}) {
  const requestId = error instanceof RequestError ? error.requestId : null;
  const message = error instanceof Error ? error.message : error;
  const [copyResult, setCopyResult] = useState<{requestId: string | null; message: string; status: 'idle' | 'success' | 'failed'} | null>(null);
  const copied = copyResult?.requestId === requestId && copyResult.message === message ? copyResult.status : 'idle';
  const copyGeneration = useRef(0);
  useEffect(() => {
    copyGeneration.current += 1;
    setCopyResult(null);
    return () => {copyGeneration.current += 1;};
  }, [requestId, message]);

  const copy = async () => {
    if (!requestId) return;
    const generation = ++copyGeneration.current;
    let success = false;
    try {success = await copyRequestId(requestId, navigator.clipboard);} catch { /* Clipboard may be unavailable. */ }
    if (generation === copyGeneration.current) setCopyResult({requestId, message, status: success ? 'success' : 'failed'});
  };

  return <div className="request-failure" role="alert">
    <p>{message}</p>
    {requestId && <div className="request-failure-diagnostic">
      <label>排错编号
        <input aria-label="排错编号" readOnly value={requestId} spellCheck={false}
          onFocus={event => event.currentTarget.select()}/>
      </label>
      <button type="button" className="btn" onClick={() => void copy()}>复制排错编号</button>
      <p className="subtle" role="status" aria-live="polite">
        {copied === 'success' ? '已复制排错编号。' : copied === 'failed'
          ? '无法自动复制，请选中上方编号手动复制。' : '可将编号提供给维护人员，定位这次请求；复制内容只有编号。'}
      </p>
    </div>}
  </div>;
}
