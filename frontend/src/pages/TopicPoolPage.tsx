import { useWorkspace } from '../components/WorkspaceContext';
import { useEffect, useState } from 'react';
import {
  api,
  CustomTopicIdea,
  CustomTopicIdeasRequest,
  CustomTopicIdeasResponse,
  Topic,
  TopicCreate,
  TopicImportPreview,
  TopicImportUrl,
  TopicSuggestion,
} from '../api/client';

const STATUS_LABELS: Record<string, string> = {
  pending: '待生成', generated: '已生成', published: '已发布',
  reviewed: '已复盘', discarded: '放弃',
};

const SOURCE_LABELS: Record<string, string> = {
  aihot: 'AI HOT', github: 'GitHub', manual: '手动录入',
  official_blog: '官方博客', other: '其他',
  custom_research: '自定义调研', custom_idea: '主题灵感',
};

const CUSTOM_CONTENT_TYPES = [
  { value: 'auto', label: '自动判断' },
  { value: 'tutorial', label: '教程' },
  { value: 'pitfall', label: '避坑' },
  { value: 'tool_review', label: '工具测评' },
  { value: 'case_study', label: '案例复盘' },
  { value: 'opinion', label: '观点' },
  { value: 'dev_log', label: '开发日志' },
];

const DEFAULT_CUSTOM_FORM: CustomTopicIdeasRequest = {
  mode: 'research',
  research_depth: 'quick',
  theme: '',
  target_audience: '',
  viewpoint: '',
  personal_case: '',
  content_type: 'auto',
  source_urls: [],
  provider: 'local',
  model: '',
};

export default function TopicPoolPage() {
  const {canWrite,canReview}=useWorkspace();
  const [topics, setTopics] = useState<Topic[]>([]);
  const [total, setTotal] = useState(0);
  const [statusFilter, setStatusFilter] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [showImportForm, setShowImportForm] = useState(false);
  const [showCustomForm, setShowCustomForm] = useState(false);
  const [form, setForm] = useState<TopicCreate>({ title: '', source_type: 'manual', url: '', raw_summary: '' });
  const [importForm, setImportForm] = useState<TopicImportUrl>({ url: '', source_type: '', fallback_summary: '', auto_score: false });
  const [importPreview, setImportPreview] = useState<TopicImportPreview | null>(null);
  const [selectedSuggestionIndex, setSelectedSuggestionIndex] = useState(0);
  const [customForm, setCustomForm] = useState<CustomTopicIdeasRequest>(DEFAULT_CUSTOM_FORM);
  const [customPreview, setCustomPreview] = useState<CustomTopicIdeasResponse | null>(null);
  const [selectedCustomIdeaIndex, setSelectedCustomIdeaIndex] = useState(0);
  const [customEditingIdea, setCustomEditingIdea] = useState<CustomTopicIdea | null>(null);
  const [customAutoScore, setCustomAutoScore] = useState(false);
  const [loading, setLoading] = useState(false);

  const load = () => {
    api.listTopics({ status: statusFilter || undefined }).then(d => {
      setTopics(d.items); setTotal(d.total);
    }).catch(() => {});
  };

  useEffect(() => { load(); }, [statusFilter]);

  const handleCreate = async () => {
    if (!form.title.trim()) return;
    setLoading(true);
    try {
      await api.createTopic(form);
      setShowForm(false);
      setForm({ title: '', source_type: 'manual', url: '', raw_summary: '' });
      load();
    } catch(e) { alert(String(e)); }
    setLoading(false);
  };

  const resetImportForm = () => {
    setShowImportForm(false);
    setImportForm({ url: '', source_type: '', fallback_summary: '', auto_score: false });
    setImportPreview(null);
    setSelectedSuggestionIndex(0);
  };

  const resetCustomForm = () => {
    setShowCustomForm(false);
    setCustomForm(DEFAULT_CUSTOM_FORM);
    setCustomPreview(null);
    setSelectedCustomIdeaIndex(0);
    setCustomEditingIdea(null);
    setCustomAutoScore(false);
  };

  const applySuggestion = (preview: TopicImportPreview, suggestion: TopicSuggestion): TopicImportPreview => ({
    ...preview,
    topic_title: suggestion.title,
    summary: suggestion.summary,
  });

  const handlePreviewUrl = async () => {
    if (!importForm.url.trim()) return;
    setLoading(true);
    try {
      const preview = await api.previewTopicFromUrl(importForm);
      const firstSuggestion = preview.suggestions?.[0];
      setImportPreview(firstSuggestion ? applySuggestion(preview, firstSuggestion) : preview);
      setSelectedSuggestionIndex(0);
    } catch(e) {
      alert('预览失败: ' + e + '\n\n如果网页无法抓取，可以在备用摘要里粘贴 README、文章摘要或关键段落后重试。');
    }
    setLoading(false);
  };

  const handleConfirmImport = async () => {
    if (!importPreview) return;
    setLoading(true);
    try {
      const selectedSuggestion = importPreview.suggestions?.[selectedSuggestionIndex];
      const topic = await api.confirmTopicImport({
        ...importPreview,
        content_angle: selectedSuggestion?.content_angle || '',
        target_audience: selectedSuggestion?.target_audience || '',
        suggestion_reason: selectedSuggestion?.reason || '',
        risk_tip: selectedSuggestion?.risk_tip || '',
        auto_score: Boolean(importForm.auto_score),
      });
      resetImportForm();
      load();
      alert(`已导入选题 #${topic.id}：${topic.title}`);
    } catch(e) {
      alert('确认导入失败: ' + e);
    }
    setLoading(false);
  };

  const handlePreviewCustomIdeas = async () => {
    if (!customForm.theme.trim()) return;
    setLoading(true);
    try {
      const sourceUrls = (customForm.source_urls || [])
        .join('\n')
        .split(/\n+/)
        .map(item => item.trim())
        .filter(Boolean);
      const preview = await api.generateCustomTopicIdeas({ ...customForm, source_urls: sourceUrls });
      setCustomPreview(preview);
      setSelectedCustomIdeaIndex(0);
      setCustomEditingIdea(preview.ideas?.[0] ? { ...preview.ideas[0] } : null);
    } catch(e) {
      alert('生成自定义选题失败: ' + e);
    }
    setLoading(false);
  };

  const handleConfirmCustomIdea = async () => {
    const idea = customEditingIdea || customPreview?.ideas?.[selectedCustomIdeaIndex];
    if (!idea) return;
    setLoading(true);
    try {
      const topic = await api.confirmCustomTopicIdea({
        ...idea,
        auto_score: customAutoScore,
        provider: customForm.provider,
        model: customForm.model,
      });
      resetCustomForm();
      load();
      alert(`已创建自定义选题 #${topic.id}：${topic.title}`);
    } catch(e) {
      alert('确认自定义选题失败: ' + e);
    }
    setLoading(false);
  };

  const handleDelete = async (id: number) => {
    if (!confirm('确定删除？')) return;
    await api.deleteTopic(id);
    load();
  };

  const handleScore = async (id: number) => {
    setLoading(true);
    try {
      await api.scoreTopic(id);
      load();
    } catch(e) { alert('评分失败: ' + e); }
    setLoading(false);
  };

  const handleGenerate = async (id: number) => {
    setLoading(true);
    try {
      const result = await api.generateDraft(id);
      load();
      alert(`已生成发布包 #${result.draft.id}，可到“发布包编辑”页面继续修改卡片。`);
    } catch(e) { alert('生成失败: ' + e); }
    setLoading(false);
  };

  const scoreColor = (s: number) => s >= 80 ? 'score-high' : s >= 60 ? 'score-mid' : 'score-low';

  const handleSelectCustomIdea = (idea: CustomTopicIdea, index: number) => {
    setSelectedCustomIdeaIndex(index);
    setCustomEditingIdea({ ...idea });
  };

  return (
    <div>
      <div className="page-header">
        <h1>📋 选题池</h1>
        <p>{total} 个选题 · 选择值得做的内容，一键生成发布包</p>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div className="filter-bar">
          {['', 'pending', 'generated', 'published', 'reviewed', 'discarded'].map(s => (
            <span key={s} className={`filter-pill ${statusFilter === s ? 'active' : ''}`}
                  onClick={() => setStatusFilter(s)}>
              {s ? STATUS_LABELS[s] : '全部'}
            </span>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button disabled={!canWrite} className="btn" onClick={() => setShowCustomForm(true)}>自定义创作</button>
          <button disabled={!canWrite} className="btn" onClick={() => setShowImportForm(true)}>从 URL 导入</button>
          <button disabled={!canWrite} className="btn btn-primary" onClick={() => setShowForm(true)}>+ 新增选题</button>
        </div>
      </div>

      <table>
        <thead>
          <tr>
            <th style={{ width: '35%' }}>标题</th>
            <th>来源</th>
            <th>角度</th>
            <th>评分</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {topics.length === 0 ? (
            <tr><td colSpan={6} className="empty">暂无选题，点击右上角新增</td></tr>
          ) : topics.map(t => (
            <tr key={t.id}>
              <td>
                <strong>{t.title}</strong>
                {t.concise_summary && <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>{t.concise_summary}</div>}
              </td>
              <td><span className="badge" style={{ background: '#f3f4f6' }}>{SOURCE_LABELS[t.source_type] || t.source_type}</span></td>
              <td>{t.content_angle || '-'}</td>
              <td><span className={`score ${scoreColor(t.score)}`}>{t.score}</span></td>
              <td><span className={`badge badge-${t.status}`}>{STATUS_LABELS[t.status]}</span></td>
              <td>
                <button className="btn btn-sm" style={{ marginRight: 4 }}
                        onClick={() => handleScore(t.id)} disabled={!canWrite || (loading)}>评分</button>
                <button className="btn btn-sm btn-primary" style={{ marginRight: 4 }}
                        onClick={() => handleGenerate(t.id)} disabled={!canWrite || (loading)}>
                  生成
                </button>
                <button disabled={!canWrite} className="btn btn-sm btn-danger" onClick={() => handleDelete(t.id)}>删除</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {showForm && (
        <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) setShowForm(false); }}>
          <div className="modal">
            <h2>新增选题</h2>
            <div className="form-group">
              <label>标题 *</label>
              <input disabled={!canWrite} value={form.title} onChange={e => setForm({ ...form, title: e.target.value })}
                     placeholder="输入选题标题" />
            </div>
            <div className="form-group">
              <label>来源类型</label>
              <select disabled={!canWrite} value={form.source_type} onChange={e => setForm({ ...form, source_type: e.target.value })}>
                {Object.entries(SOURCE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <div className="form-group">
              <label>来源网址</label>
              <input disabled={!canWrite} value={form.url || ''} onChange={e => setForm({ ...form, url: e.target.value })}
                     placeholder="https://..." />
            </div>
            <div className="form-group">
              <label>原始摘要</label>
              <textarea disabled={!canWrite} value={form.raw_summary || ''} onChange={e => setForm({ ...form, raw_summary: e.target.value })}
                        placeholder="粘贴原文或摘要..." />
            </div>
            <div className="form-actions">
              <button className="btn" onClick={() => setShowForm(false)}>取消</button>
              <button className="btn btn-primary" onClick={handleCreate} disabled={!canWrite || (loading)}>
                {loading ? '创建中...' : '创建选题'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showImportForm && (
        <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) resetImportForm(); }}>
          <div className="modal">
            <h2>从 URL 导入选题</h2>
            <div className="form-group">
              <label>URL *</label>
              <input disabled={!canWrite}
                value={importForm.url}
                onChange={e => { setImportForm({ ...importForm, url: e.target.value }); setImportPreview(null); }}
                placeholder="https://github.com/langchain-ai/langgraph"
              />
            </div>
            <div className="form-group">
              <label>来源类型</label>
              <select disabled={!canWrite} value={importForm.source_type || ''} onChange={e => { setImportForm({ ...importForm, source_type: e.target.value }); setImportPreview(null); }}>
                <option value="">自动判断</option>
                <option value="github">GitHub</option>
                <option value="official_blog">官方博客</option>
                <option value="aihot">AI HOT</option>
                <option value="other">其他</option>
              </select>
            </div>
            <div className="form-group">
              <label>备用摘要</label>
              <textarea disabled={!canWrite}
                value={importForm.fallback_summary || ''}
                onChange={e => { setImportForm({ ...importForm, fallback_summary: e.target.value }); setImportPreview(null); }}
                placeholder="如果网页或 GitHub 读取失败，可以在这里粘贴 README、文章摘要或关键段落。"
              />
            </div>
            <label className="checkbox-row">
              <input disabled={!canWrite}
                type="checkbox"
                checked={Boolean(importForm.auto_score)}
                onChange={e => setImportForm({ ...importForm, auto_score: e.target.checked })}
              />
              <span>导入后自动评分</span>
            </label>
            {importPreview && (
              <div className="import-preview">
                <h3>导入预览</h3>
                {(importPreview.suggestions || []).length > 0 && (
                  <div className="suggestion-grid">
                    {importPreview.suggestions.map((suggestion, index) => (
                      <button disabled={!canWrite}
                        key={`${suggestion.title}-${index}`}
                        className={`suggestion-card ${selectedSuggestionIndex === index ? 'active' : ''}`}
                        onClick={() => {
                          setSelectedSuggestionIndex(index);
                          setImportPreview(applySuggestion(importPreview, suggestion));
                        }}
                      >
                        <strong>{suggestion.title}</strong>
                        <span>{suggestion.content_angle} · {suggestion.target_audience}</span>
                        <small>{suggestion.reason}</small>
                      </button>
                    ))}
                  </div>
                )}
                <div className="form-group">
                  <label>选题标题</label>
                  <input disabled={!canWrite}
                    value={importPreview.topic_title}
                    onChange={e => setImportPreview({ ...importPreview, topic_title: e.target.value })}
                  />
                </div>
                <div className="form-group">
                  <label>信源标题</label>
                  <input disabled={!canWrite}
                    value={importPreview.title}
                    onChange={e => setImportPreview({ ...importPreview, title: e.target.value })}
                  />
                </div>
                <div className="form-group">
                  <label>确认来源类型</label>
                  <select disabled={!canWrite} value={importPreview.source_type} onChange={e => setImportPreview({ ...importPreview, source_type: e.target.value })}>
                    <option value="github">GitHub</option>
                    <option value="official_blog">官方博客</option>
                    <option value="aihot">AI HOT</option>
                    <option value="other">其他</option>
                  </select>
                </div>
                <div className="form-group">
                  <label>选题摘要</label>
                  <textarea disabled={!canWrite}
                    value={importPreview.summary}
                    onChange={e => setImportPreview({ ...importPreview, summary: e.target.value })}
                    rows={5}
                  />
                </div>
              </div>
            )}
            <div className="form-actions">
              <button className="btn" onClick={resetImportForm}>取消</button>
              <button className="btn" onClick={handlePreviewUrl} disabled={!canWrite || (loading)}>
                {loading ? '读取中...' : '预览信源'}
              </button>
              <button className="btn btn-primary" onClick={handleConfirmImport} disabled={!canWrite || (loading || !importPreview)}>
                {loading ? '导入中...' : '确认导入'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showCustomForm && (
        <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) resetCustomForm(); }}>
          <div className="modal custom-topic-modal">
            <h2>自定义选题创作</h2>
            <div className="custom-mode-grid">
              <button disabled={!canWrite}
                className={`custom-mode-card ${customForm.mode === 'research' ? 'active' : ''}`}
                onClick={() => { setCustomForm({ ...customForm, mode: 'research' }); setCustomPreview(null); }}
              >
                <strong>AI 自动调研</strong>
                <span>适合只有方向时，让 Agent 先整理中文公开资料和选题角度。</span>
              </button>
              <button disabled={!canWrite}
                className={`custom-mode-card ${customForm.mode === 'inspiration' ? 'active' : ''}`}
                onClick={() => { setCustomForm({ ...customForm, mode: 'inspiration' }); setCustomPreview(null); }}
              >
                <strong>主题灵感创作</strong>
                <span>适合你已有主题、观点或个人案例，让 AI 生成多个选题。</span>
              </button>
            </div>

            <div className="form-group">
              <label>主题 *</label>
              <input disabled={!canWrite}
                value={customForm.theme}
                onChange={e => { setCustomForm({ ...customForm, theme: e.target.value }); setCustomPreview(null); }}
                placeholder="例如：普通人怎么用 AI 自动化副业内容"
              />
            </div>

            <div className="form-row">
              <div className="form-group">
                <label>目标人群</label>
                <input disabled={!canWrite}
                  value={customForm.target_audience || ''}
                  onChange={e => { setCustomForm({ ...customForm, target_audience: e.target.value }); setCustomPreview(null); }}
                  placeholder="AI 新手 / 职场人 / 自媒体人"
                />
              </div>
              <div className="form-group">
                <label>内容类型</label>
                <select disabled={!canWrite}
                  value={customForm.content_type || 'auto'}
                  onChange={e => { setCustomForm({ ...customForm, content_type: e.target.value }); setCustomPreview(null); }}
                >
                  {CUSTOM_CONTENT_TYPES.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
              </div>
            </div>

            <div className="form-row">
              <div className="form-group">
                <label>调研深度</label>
                <select disabled={!canWrite}
                  value={customForm.research_depth}
                  onChange={e => { setCustomForm({ ...customForm, research_depth: e.target.value as 'quick' | 'deep' }); setCustomPreview(null); }}
                >
                  <option value="quick">快速模式</option>
                  <option value="deep">深度模式</option>
                </select>
              </div>
              <div className="form-group">
                <label>模型</label>
                <select disabled={!canWrite}
                  value={customForm.provider || 'local'}
                  onChange={e => { setCustomForm({ ...customForm, provider: e.target.value }); setCustomPreview(null); }}
                >
                  <option value="local">local 规则模型</option>
                  <option value="doubao">豆包 / 火山方舟</option>
                  <option value="deepseek">DeepSeek</option>
                </select>
              </div>
            </div>

            <div className="form-group">
              <label>想表达的观点</label>
              <textarea disabled={!canWrite}
                value={customForm.viewpoint || ''}
                onChange={e => { setCustomForm({ ...customForm, viewpoint: e.target.value }); setCustomPreview(null); }}
                placeholder="例如：普通人不要追求全自动，先把一个小流程跑通。"
                rows={3}
              />
            </div>

            <div className="form-group">
              <label>个人经验 / 案例</label>
              <textarea disabled={!canWrite}
                value={customForm.personal_case || ''}
                onChange={e => { setCustomForm({ ...customForm, personal_case: e.target.value }); setCustomPreview(null); }}
                placeholder="可选。没有真实案例也可以留空，系统会标记为观点创作/未核验。"
                rows={3}
              />
            </div>

            {customForm.mode === 'research' && (
              <div className="form-group">
                <label>补充来源链接</label>
                <textarea disabled={!canWrite}
                  value={(customForm.source_urls || []).join('\n')}
                  onChange={e => {
                    setCustomForm({ ...customForm, source_urls: e.target.value.split(/\n+/).map(item => item.trim()).filter(Boolean) });
                    setCustomPreview(null);
                  }}
                  placeholder="可选，每行一个中文公开网页 / GitHub / 工具官网链接。没有链接时会生成搜索关键词并标记未核验。"
                  rows={3}
                />
              </div>
            )}

            <label className="checkbox-row">
              <input disabled={!canWrite}
                type="checkbox"
                checked={customAutoScore}
                onChange={e => setCustomAutoScore(e.target.checked)}
              />
              <span>确认入库后自动评分</span>
            </label>

            {customPreview && (
              <div className="import-preview">
                <h3>选题建议</h3>
                <div className="custom-preview-meta">
                  <span>{customPreview.mode === 'research' ? 'AI 自动调研' : '主题灵感'}</span>
                  <span>{customPreview.research_depth === 'deep' ? '深度模式' : '快速模式'}</span>
                  <span>{customPreview.research_status}</span>
                </div>
                {customPreview.keywords.length > 0 && (
                  <div className="keyword-row">
                    {customPreview.keywords.map(keyword => <span key={keyword}>{keyword}</span>)}
                  </div>
                )}
                {customPreview.references.length > 0 && (
                  <details className="custom-reference-details">
                    <summary>参考来源（{customPreview.references.length}）</summary>
                    {customPreview.references.map((ref, index) => (
                      <div key={`${ref.url}-${index}`} className="custom-reference-item">
                        <strong>{ref.title}</strong>
                        <span>{ref.status} · {ref.source_type}</span>
                        <small>{ref.summary}</small>
                      </div>
                    ))}
                  </details>
                )}
                <div className="suggestion-grid">
                  {customPreview.ideas.map((idea, index) => (
                    <button disabled={!canWrite}
                      key={`${idea.title}-${index}`}
                      className={`suggestion-card ${selectedCustomIdeaIndex === index ? 'active' : ''}`}
                      onClick={() => handleSelectCustomIdea(idea, index)}
                    >
                      <strong>{idea.title}</strong>
                      <span>{idea.content_angle} · {idea.target_audience} · {idea.score}分</span>
                      <small>{idea.reason}</small>
                      <small>{idea.verification_status}</small>
                      {idea.duplicate_hint && <small style={{ color: '#92400e' }}>{idea.duplicate_hint}</small>}
                    </button>
                  ))}
                </div>
                {customEditingIdea && (
                  <div className="source-idea-editor custom-idea-editor">
                    <div className="source-idea-editor__header">
                      <h3>入库前编辑</h3>
                      <span className="badge" style={{ background: '#eef2ff', color: '#4338ca' }}>
                        {customEditingIdea.verification_status}
                      </span>
                    </div>
                    <div className="form-group">
                      <label>标题</label>
                      <input disabled={!canWrite}
                        value={customEditingIdea.title}
                        onChange={e => setCustomEditingIdea({ ...customEditingIdea, title: e.target.value })}
                      />
                    </div>
                    <div className="form-row">
                      <div className="form-group">
                        <label>内容角度</label>
                        <input disabled={!canWrite}
                          value={customEditingIdea.content_angle}
                          onChange={e => setCustomEditingIdea({ ...customEditingIdea, content_angle: e.target.value })}
                        />
                      </div>
                      <div className="form-group">
                        <label>评分</label>
                        <input disabled={!canWrite}
                          type="number"
                          min={0}
                          max={100}
                          value={customEditingIdea.score}
                          onChange={e => setCustomEditingIdea({ ...customEditingIdea, score: Number(e.target.value) })}
                        />
                      </div>
                    </div>
                    <div className="form-group">
                      <label>目标人群</label>
                      <input disabled={!canWrite}
                        value={customEditingIdea.target_audience}
                        onChange={e => setCustomEditingIdea({ ...customEditingIdea, target_audience: e.target.value })}
                      />
                    </div>
                    <div className="form-group">
                      <label>摘要</label>
                      <textarea disabled={!canWrite}
                        rows={4}
                        value={customEditingIdea.summary}
                        onChange={e => setCustomEditingIdea({ ...customEditingIdea, summary: e.target.value })}
                      />
                    </div>
                    <div className="form-row">
                      <div className="form-group">
                        <label>推荐理由</label>
                        <textarea disabled={!canWrite}
                          rows={3}
                          value={customEditingIdea.reason}
                          onChange={e => setCustomEditingIdea({ ...customEditingIdea, reason: e.target.value })}
                        />
                      </div>
                      <div className="form-group">
                        <label>风险提醒</label>
                        <textarea disabled={!canWrite}
                          rows={3}
                          value={customEditingIdea.risk_tip}
                          onChange={e => setCustomEditingIdea({ ...customEditingIdea, risk_tip: e.target.value })}
                        />
                      </div>
                    </div>
                    {customEditingIdea.duplicate_hint && (
                      <div className="source-duplicate-hint">{customEditingIdea.duplicate_hint}</div>
                    )}
                  </div>
                )}
              </div>
            )}

            <div className="form-actions">
              <button className="btn" onClick={resetCustomForm}>取消</button>
              <button className="btn" onClick={handlePreviewCustomIdeas} disabled={!canWrite || (loading || !customForm.theme.trim())}>
                {loading ? '生成中...' : '生成 5 个建议'}
              </button>
              <button className="btn btn-primary" onClick={handleConfirmCustomIdea} disabled={!canWrite || (loading || !customPreview)}>
                {loading ? '入库中...' : '确认入库'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
