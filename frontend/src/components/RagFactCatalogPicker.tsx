import {useEffect, useState} from 'react';
import {api, RagFactCatalog, RequiredFact} from '../api/client';
import {safeEvidenceUrl} from '../api/evidence';

export default function RagFactCatalogPicker({knowledgeBaseId, workspaceId, disabled, onAdd}: {
  knowledgeBaseId: number; workspaceId?: number; disabled: boolean; onAdd: (fact: RequiredFact) => void;
}) {
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [catalog, setCatalog] = useState<RagFactCatalog | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setBusy(true); setCatalog(null); setError('');
    api.ragFactCatalog(knowledgeBaseId, workspaceId, offset, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      if (result.knowledge_base_id !== knowledgeBaseId || (workspaceId != null && result.workspace_id !== workspaceId)) {
        throw new Error('参数目录范围已变化，请重新打开。');
      }
      setCatalog(result);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => {if (!controller.signal.aborted) setBusy(false);});
    return () => controller.abort();
  }, [open, knowledgeBaseId, workspaceId, offset, revision]);

  return <div className="rag-catalog">
    <button type="button" className="btn" disabled={disabled} aria-expanded={open} onClick={() => setOpen(value => !value)}>
      {open ? '收起参数目录' : '从已核验资料选择参数'}
    </button>
    {open && <div className="rag-catalog-body">
      <p className="subtle">这里列的是资料中登记的参数，不是系统对问题的理解。点击后加入下方清单，再核对型号，并补上你需要但目录中没有的参数。</p>
      {busy && <p role="status">正在读取当前知识库的参数…</p>}
      {error && <p role="alert">{error}</p>}
      {catalog && <>
        <p className="subtle">当前目录共 {catalog.total} 项。{catalog.items.length > 0 ? `本页 ${catalog.offset + 1}–${catalog.offset + catalog.items.length} 项。` : '当前页没有可选项，可刷新目录或手动填写。'}</p>
        <ul className="rag-catalog-items">
          {catalog.items.map(item => <li key={JSON.stringify([item.product_model, item.parameter])}>
            <button type="button" className="btn" disabled={disabled} onClick={() => onAdd(item)}>加入 {item.product_model} / {item.parameter}</button>
            <details><summary>查看登记出处 · {item.evidence.length} 条</summary>
              {item.evidence.map(evidence => <p className="subtle" key={`${evidence.document_id}:${evidence.chunk_id}:${evidence.locator}`}>
                版本 {evidence.version_label} · {evidence.locator} · 片段 #{evidence.chunk_id}
                {safeEvidenceUrl(evidence.source_url) && <> · <a href={safeEvidenceUrl(evidence.source_url)!} target="_blank" rel="noopener noreferrer">查看原始来源</a></>}
              </p>)}
            </details>
          </li>)}
        </ul>
        <div className="form-actions">
          <button type="button" className="btn" disabled={disabled || busy || !offset} onClick={() => setOffset(Math.max(0, offset - catalog.limit))}>上一页参数</button>
          <button type="button" className="btn" disabled={disabled || busy || !catalog.has_more} onClick={() => setOffset(offset + catalog.limit)}>下一页参数</button>
        </div>
      </>}
      <button type="button" className="btn" disabled={disabled || busy} onClick={() => {setOffset(0); setRevision(value => value + 1);}}>刷新参数目录</button>
      <p className="subtle">目录可能随资料核验状态变化；正式提问时会重新检查依据。目录为空不代表这个产品没有该参数。</p>
    </div>}
  </div>;
}
