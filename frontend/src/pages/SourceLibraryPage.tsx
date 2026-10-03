import { useWorkspace } from '../components/WorkspaceContext';
import { useEffect, useMemo, useState } from 'react';
import {
  SourceDetail,
  SourceItem,
  SourceStats,
  SourceTopicIdea,
  SourceTopicIdeasResponse,
  RagIndexSourceResult,
  api,
} from '../api/client';

const TYPE_LABELS: Record<string, string> = {
  aihot: 'AI HOT',
  github: 'GitHub',
  manual: '手动录入',
  official_blog: '官方博客',
  custom_idea: '自定义创作',
  custom_research: '自定义调研',
  other: '其他',
};

const CONTENT_TYPES = [
  { value: 'auto', label: '自动判断' },
  { value: 'tutorial', label: '教程' },
  { value: 'pitfall', label: '避坑' },
  { value: 'tool_review', label: '工具测评' },
  { value: 'case_study', label: '案例复盘' },
  { value: 'opinion', label: '观点' },
  { value: 'dev_log', label: '开发日志' },
];

const emptyIdea: SourceTopicIdea = {
  title: '',
  content_angle: '',
  target_audience: '',
  summary: '',
  reason: '',
  risk_tip: '',
  recommended_platform: '小红书图文',
  source_type: 'source_library',
  score: 65,
  keywords: [],
  references: [],
  verification_status: '有公开来源待人工核验',
  duplicate_hint: '',
};

export default function SourceLibraryPage() {
  const {canWrite,canReview}=useWorkspace();
  const [sources, setSources] = useState<SourceItem[]>([]);
  const [stats, setStats] = useState<SourceStats | null>(null);
  const [typeFilter, setTypeFilter] = useState('');
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<SourceDetail | null>(null);
  const [ideasResult, setIdeasResult] = useState<SourceTopicIdeasResponse | null>(null);
  const [selectedIdeaIndex, setSelectedIdeaIndex] = useState<number | null>(null);
  const [editingIdea, setEditingIdea] = useState<SourceTopicIdea>(emptyIdea);
  const [targetAudience, setTargetAudience] = useState('AI 新手 / 自媒体人');
  const [contentType, setContentType] = useState('auto');
  const [provider, setProvider] = useState('local');
  const [autoScore, setAutoScore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [ragIndexing, setRagIndexing] = useState(false);
  const [ragIndexResult, setRagIndexResult] = useState<RagIndexSourceResult | null>(null);
  const [ragIndexError, setRagIndexError] = useState('');

  const selectedSource = useMemo(
    () => sources.find(item => item.id === selectedId) || null,
    [sources, selectedId],
  );

  const load = () => {
    api.listSources({ source_type: typeFilter || undefined, limit: 50 }).then(data => {
      setSources(data.items);
      setTotal(data.total);
      if (!selectedId && data.items.length > 0) setSelectedId(data.items[0].id);
    }).catch(() => {
      setSources([]);
      setTotal(0);
    });
    api.getSourceStats().then(setStats).catch(() => setStats(null));
  };

  useEffect(() => { load(); }, [typeFilter]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    api.getSource(selectedId).then(setDetail).catch(() => setDetail(null));
    setIdeasResult(null);
    setSelectedIdeaIndex(null);
    setEditingIdea(emptyIdea);
    setRagIndexResult(null);
    setRagIndexError('');
  }, [selectedId]);

  const handleGenerateIdeas = async () => {
    if (!selectedId) return;
    setLoading(true);
    try {
      const result = await api.generateSourceTopicIdeas(selectedId, {
        target_audience: targetAudience,
        content_type: contentType,
        provider,
        limit: 5,
      });
      setIdeasResult(result);
      const first = result.ideas[0] || emptyIdea;
      setSelectedIdeaIndex(result.ideas.length > 0 ? 0 : null);
      setEditingIdea({ ...first });
    } catch (e) {
      alert('生成素材选题失败: ' + e);
    }
    setLoading(false);
  };

  const handleIndexForRag = async () => {
    if (!selectedId) return;
    setRagIndexing(true);
    setRagIndexError('');
    try {
      const result = await api.indexSourceForRag({ source_id: selectedId });
      setRagIndexResult(result);
      await Promise.all([
        api.getSource(selectedId).then(setDetail),
        api.listSources({ source_type: typeFilter || undefined, limit: 50 }).then(data => {
          setSources(data.items);
          setTotal(data.total);
        }),
      ]);
    } catch (e) {
      setRagIndexError(e instanceof Error ? e.message : String(e));
    }
    setRagIndexing(false);
  };

  const handleSelectIdea = (idea: SourceTopicIdea, index: number) => {
    setSelectedIdeaIndex(index);
    setEditingIdea({ ...idea });
  };

  const handleConfirmIdea = async () => {
    if (!selectedId || !editingIdea.title.trim()) return;
    setLoading(true);
    try {
      const topic = await api.confirmSourceTopicIdea(selectedId, {
        ...editingIdea,
        title: editingIdea.title.trim(),
        score: Number(editingIdea.score) || 65,
        auto_score: autoScore,
        provider,
      });
      alert(`已创建选题 #${topic.id}：${topic.title}`);
      await Promise.all([
        api.getSource(selectedId).then(setDetail),
        api.listSources({ source_type: typeFilter || undefined, limit: 50 }).then(data => {
          setSources(data.items);
          setTotal(data.total);
        }),
        api.getSourceStats().then(setStats),
      ]);
      setIdeasResult(null);
      setSelectedIdeaIndex(null);
      setEditingIdea(emptyIdea);
    } catch (e) {
      alert('确认入库失败: ' + e);
    }
    setLoading(false);
  };

  return (
    <div>
      <div className="page-header">
        <h1>选题素材库</h1>
        <p>{total} 个素材 · 从同一条素材拆出不同角度的选题</p>
      </div>

      {stats && (
        <div className="source-stat-grid">
          <div className="stat-card">
            <div className="stat-value">{stats.total_sources}</div>
            <div className="stat-label">素材总数</div>
          </div>
          <div className="stat-card">
            <div className="stat-value" style={{ color: 'var(--accent)' }}>{stats.total_topics_from_sources}</div>
            <div className="stat-label">已生成选题</div>
          </div>
          {stats.by_type.slice(0, 4).map(item => (
            <div key={item.source_type} className="stat-card">
              <div className="stat-value" style={{ color: 'var(--blue)' }}>{item.count}</div>
              <div className="stat-label">{TYPE_LABELS[item.source_type] || item.source_type}</div>
            </div>
          ))}
        </div>
      )}

      <div className="filter-bar">
        {['', 'github', 'aihot', 'manual', 'official_blog', 'custom_idea', 'custom_research', 'other'].map(item => (
          <span
            key={item}
            className={`filter-pill ${typeFilter === item ? 'active' : ''}`}
            onClick={() => { setTypeFilter(item); setSelectedId(null); }}
          >
            {item ? TYPE_LABELS[item] || item : '全部'}
          </span>
        ))}
      </div>

      <div className="source-workspace">
        <section className="source-list-panel">
          <div className="source-list-panel__header">
            <h2>素材列表</h2>
            <span>{sources.length} 条</span>
          </div>
          {sources.length === 0 ? (
            <div className="empty" style={{ padding: 16 }}>暂无素材，通过 URL 导入或自定义创作后会自动记录</div>
          ) : (
            <div className="source-list">
              {sources.map(source => (
                <button
                  key={source.id}
                  className={`source-list-item ${selectedId === source.id ? 'active' : ''}`}
                  onClick={() => setSelectedId(source.id)}
                >
                  <strong>{source.title}</strong>
                  <span>{TYPE_LABELS[source.source_type] || source.source_type} · {source.topic_count} 个选题</span>
                  {source.quality_flags && source.quality_flags.length > 0 && (
                    <div className="source-quality-row">
                      {source.quality_flags.slice(0, 3).map(flag => <em key={flag}>{flag}</em>)}
                    </div>
                  )}
	                  {source.summary && <p>{source.summary.slice(0, 110)}</p>}
	                  {source.rag_index?.indexed && (
	                    <small className="source-inline-success">
	                      已索引到知识库 #{source.rag_index.knowledge_base_id} · {source.rag_index.chunk_count} chunks
	                    </small>
	                  )}
	                  {source.duplicate_hint && <small className="source-inline-warning">{source.duplicate_hint}</small>}
	                </button>
	              ))}
            </div>
          )}
        </section>

        <section className="source-detail-panel">
          {!selectedSource || !detail ? (
            <div className="empty" style={{ padding: 20 }}>选择一个素材后，可以在这里生成多个选题角度</div>
          ) : (
            <>
              <div className="source-detail-header">
                <div>
                  <span className="badge" style={{ background: '#f3f4f6' }}>
                    {TYPE_LABELS[selectedSource.source_type] || selectedSource.source_type}
                  </span>
                  <h2>{detail.title}</h2>
                  <p>{detail.summary || '暂无摘要'}</p>
                  {detail.quality_flags && detail.quality_flags.length > 0 && (
                    <div className="source-quality-row source-quality-row--detail">
                      {detail.quality_flags.map(flag => <em key={flag}>{flag}</em>)}
                    </div>
                  )}
                  {detail.duplicate_hint && <div className="source-duplicate-hint">{detail.duplicate_hint}</div>}
                  <div className="source-detail-actions">
                    {detail.url && /^https?:\/\//i.test(detail.url) && <a href={detail.url} target="_blank" rel="noreferrer">查看原文</a>}
                    <button className="btn btn-sm" onClick={handleIndexForRag} disabled={!canWrite || (ragIndexing)}>
                      {ragIndexing ? '索引中...' : '索引到知识库'}
                    </button>
                  </div>
                  {(ragIndexResult || ragIndexError) && (
                    <div className={`source-rag-status ${ragIndexError ? 'error' : 'ok'}`}>
                      {ragIndexError || `已写入知识库 #${ragIndexResult?.knowledge_base_id}，生成 ${ragIndexResult?.chunk_count} 个 chunk`}
                    </div>
                  )}
                  {detail.rag_index && (
                    <div className={`source-rag-status ${detail.rag_index.indexed ? 'ok' : 'idle'}`}>
                      {detail.rag_index.indexed
                        ? `当前已索引：知识库 #${detail.rag_index.knowledge_base_id} · ${detail.rag_index.chunk_count} 个 chunk`
                        : `当前未索引到默认知识库 #${detail.rag_index.knowledge_base_id}`}
                    </div>
                  )}
                </div>
              </div>

              <div className="source-topic-history">
                <h3>已有选题</h3>
                {detail.topics.length === 0 ? (
                  <p>这条素材还没有生成过选题。</p>
                ) : (
                  <div className="source-topic-list">
                    {detail.topics.slice(0, 6).map(topic => (
                      <div key={topic.id}>
                        <strong>#{topic.id} {topic.title}</strong>
                        <span>{topic.content_angle || '未分类'} · {topic.score}分 · {topic.status}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="source-idea-toolbar">
                <div className="form-row">
                  <div className="form-group">
                    <label>目标人群</label>
                    <input disabled={!canWrite} value={targetAudience} onChange={e => setTargetAudience(e.target.value)} />
                  </div>
                  <div className="form-group">
                    <label>内容类型</label>
                    <select disabled={!canWrite} value={contentType} onChange={e => setContentType(e.target.value)}>
                      {CONTENT_TYPES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                  </div>
                  <div className="form-group">
                    <label>模型</label>
                    <select disabled={!canWrite} value={provider} onChange={e => setProvider(e.target.value)}>
                      <option value="local">local 规则模型</option>
                      <option value="doubao">豆包 / 火山方舟</option>
                      <option value="deepseek">DeepSeek</option>
                    </select>
                  </div>
                </div>
                <button className="btn btn-primary" onClick={handleGenerateIdeas} disabled={!canWrite || (loading)}>
                  {loading ? '生成中...' : '从这条素材生成 5 个角度'}
                </button>
              </div>

              {ideasResult && (
                <div className="source-ideas-panel">
                  <div className="source-ideas-meta">
                    <span>{ideasResult.research_status}</span>
                    <p>{ideasResult.keywords.slice(0, 6).join(' · ')}</p>
                  </div>
                  <div className="source-idea-grid">
                    {ideasResult.ideas.map((idea, index) => (
                      <button
                        key={`${idea.title}-${index}`}
                        className={`source-idea-card ${selectedIdeaIndex === index ? 'active' : ''}`}
                        onClick={() => handleSelectIdea(idea, index)}
                      >
                        <strong>{idea.title}</strong>
                        <span>{idea.content_angle} · {idea.score}分 · {idea.verification_status}</span>
                        <p>{idea.reason}</p>
                        {idea.duplicate_hint && <em>{idea.duplicate_hint}</em>}
                      </button>
                    ))}
                  </div>

                  <div className="source-idea-editor">
                    <div className="source-idea-editor__header">
                      <h3>入库前编辑</h3>
                      <label className="checkbox-row" style={{ margin: 0 }}>
                        <input disabled={!canWrite} type="checkbox" checked={autoScore} onChange={e => setAutoScore(e.target.checked)} />
                        <span>入库后自动评分</span>
                      </label>
                    </div>
                    <div className="form-group">
                      <label>标题</label>
                      <input disabled={!canWrite} value={editingIdea.title} onChange={e => setEditingIdea({ ...editingIdea, title: e.target.value })} />
                    </div>
                    <div className="form-row">
                      <div className="form-group">
                        <label>内容角度</label>
                        <input disabled={!canWrite} value={editingIdea.content_angle} onChange={e => setEditingIdea({ ...editingIdea, content_angle: e.target.value })} />
                      </div>
                      <div className="form-group">
                        <label>评分</label>
                        <input disabled={!canWrite}
                          type="number"
                          min={0}
                          max={100}
                          value={editingIdea.score}
                          onChange={e => setEditingIdea({ ...editingIdea, score: Number(e.target.value) })}
                        />
                      </div>
                    </div>
                    <div className="form-group">
                      <label>目标人群</label>
                      <input disabled={!canWrite} value={editingIdea.target_audience} onChange={e => setEditingIdea({ ...editingIdea, target_audience: e.target.value })} />
                    </div>
                    <div className="form-group">
                      <label>摘要</label>
                      <textarea disabled={!canWrite} rows={4} value={editingIdea.summary} onChange={e => setEditingIdea({ ...editingIdea, summary: e.target.value })} />
                    </div>
                    <div className="form-row">
                      <div className="form-group">
                        <label>推荐理由</label>
                        <textarea disabled={!canWrite} rows={3} value={editingIdea.reason} onChange={e => setEditingIdea({ ...editingIdea, reason: e.target.value })} />
                      </div>
                      <div className="form-group">
                        <label>风险提醒</label>
                        <textarea disabled={!canWrite} rows={3} value={editingIdea.risk_tip} onChange={e => setEditingIdea({ ...editingIdea, risk_tip: e.target.value })} />
                      </div>
                    </div>
                    {editingIdea.duplicate_hint && <div className="source-duplicate-hint">{editingIdea.duplicate_hint}</div>}
                    <button className="btn btn-primary" onClick={handleConfirmIdea} disabled={!canWrite || (loading || !editingIdea.title.trim())}>
                      {loading ? '入库中...' : '确认入库为新选题'}
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
