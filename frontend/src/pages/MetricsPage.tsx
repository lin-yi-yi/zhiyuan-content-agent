import { useWorkspace } from '../components/WorkspaceContext';
import { useEffect, useMemo, useState } from 'react';
import { api, ContentPrediction, ContentPredictionCreate, Draft, Metric, MetricCreate, PublishLog, Topic } from '../api/client';
import { latestMetricSnapshot } from '../utils/metricSnapshot';

const PLATFORM_LABELS: Record<string, string> = {
  xiaohongshu: '小红书',
  douyin: '抖音',
};

function rate(value: number, base: number) {
  if (!base) return null;
  return value / base;
}

function pct(value: number, base: number) {
  return ratePercent(rate(value, base));
}

const emptyMetric: MetricCreate = {
  views: 0,
  likes: 0,
  favorites: 0,
  comments: 0,
  shares: 0,
  new_followers: 0,
  impressions: null,
  click_rate: null,
  profile_visits: null,
  follow_conversion_rate: null,
  notes: '',
};

const emptyPrediction: ContentPredictionCreate = {
  draft_id: 0,
  platform: 'xiaohongshu',
  predicted_views: 0,
  predicted_likes: 0,
  predicted_favorites: 0,
  predicted_comments: 0,
  predicted_shares: 0,
  predicted_new_followers: 0,
  confidence: 60,
  rubric_version: 'manual-v1',
  rationale: '',
  risk_notes: '',
};

function numberValue(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function ratePercent(value: number | null | undefined) {
  return value==null?'暂无有效数据':`${(value * 100).toFixed(1)}%`;
}

export default function MetricsPage() {
  const {canWrite,canReview}=useWorkspace();
  const [topics, setTopics] = useState<Topic[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [logs, setLogs] = useState<PublishLog[]>([]);
  const [selectedLogId, setSelectedLogId] = useState<number | null>(null);
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [predictions, setPredictions] = useState<ContentPrediction[]>([]);
  const [loading, setLoading] = useState(false);
  const [dataLoaded,setDataLoaded]=useState(false);
  const [predictionLoading,setPredictionLoading]=useState(false);
  const [predictionReadError,setPredictionReadError]=useState(false);
  const [predictionReload,setPredictionReload]=useState(0);
  const [error,setError]=useState('');
  const [message,setMessage]=useState('');
  const [logForm, setLogForm] = useState({
    draft_id: 0,
    platform: 'xiaohongshu',
    published_at: '',
    post_url: '',
    used_title: '',
    used_cover_text: '',
    content_type: '图文',
    notes: '',
  });
  const [metricForm, setMetricForm] = useState<MetricCreate>(emptyMetric);
  const [predictionForm, setPredictionForm] = useState<ContentPredictionCreate>(emptyPrediction);

  const topicById = useMemo(() => new Map(topics.map(t => [t.id, t])), [topics]);
  const draftById = useMemo(() => new Map(drafts.map(d => [d.id, d])), [drafts]);
  const selectedDraft = draftById.get(logForm.draft_id);
  const selectedLog = logs.find(log => log.id === selectedLogId) || null;
  const latestPrediction = predictions[0] || null;
  const alreadyPublished=logs.some(log=>log.draft_id===logForm.draft_id&&log.platform===logForm.platform);
  const predictionLocked=selectedLogId!==null||alreadyPublished||Boolean(latestPrediction&&(latestPrediction.status!=='draft'||latestPrediction.publish_log_id!==null));
  const predictionDisabled=!canWrite||loading||predictionLoading||predictionReadError||!dataLoaded||predictionLocked||!logForm.draft_id;

  const load = async () => {
    const [topicData, draftData, logData] = await Promise.all([
      api.listTopics({ limit: 100 }),
      api.listDrafts({ limit: 100 }),
      api.listPublishLogs(),
    ]);
    setTopics(topicData.items);
    setDrafts(draftData);
    setLogs(logData);
    if (!logForm.draft_id && draftData.length > 0) {
      const first = draftData.find(draft=>!logData.some(log=>log.draft_id===draft.id&&log.platform===logForm.platform))||draftData[0];
      setLogForm(prev => ({
        ...prev,
        draft_id: first.id,
        used_title: first.title_options?.[0] || '',
        used_cover_text: first.cover_text_options?.[0] || '',
      }));
    }
    setDataLoaded(true);
  };

  useEffect(() => { load().catch(e => setError(`基础资料读取失败：${e.message}`)); }, []);

  useEffect(() => {
    let active=true;setMetrics([]);setMetricForm({...emptyMetric});
    if (!selectedLogId) {
      setMetrics([]);
      return;
    }
    api.listMetrics(selectedLogId).then(items=>{if(active)setMetrics(items);}).catch(e=>{if(active)setError(`指标读取失败：${e.message}`);});
    return()=>{active=false;};
  }, [selectedLogId]);

  useEffect(() => {
    let active=true;
    const targetDraftId = selectedLog?.draft_id || logForm.draft_id;
    const platform=selectedLog?.platform||logForm.platform;
    setPredictionForm({...emptyPrediction,draft_id:targetDraftId||0,platform});setPredictions([]);setPredictionReadError(false);
    if(!targetDraftId&&!selectedLogId){setPredictionLoading(false);return;}
    setPredictionLoading(true);
    api.listPredictions(selectedLogId?{publish_log_id:selectedLogId}:{draft_id:targetDraftId}).then(items=>{
      if(!active)return;
      const filtered=items.filter(item=>item.platform===platform).sort((a,b)=>b.id-a.id);
      setPredictions(filtered);
      const latest=filtered[0];
      if(latest){const {id:_,created_at:__,updated_at:___,publish_log_id:____,status:_____,...values}=latest;setPredictionForm({...values,publish_log_id:null});}
    }).catch(e=>{if(active){setPredictionReadError(true);setError(`预测读取失败：${e.message}`);}}).finally(()=>{if(active)setPredictionLoading(false);});
    return()=>{active=false;};
  }, [selectedLogId, selectedLog?.draft_id, selectedLog?.platform, logForm.draft_id, logForm.platform,predictionReload]);

  const draftLabel = (draft: Draft) => {
    const topic = topicById.get(draft.topic_id);
    return `${topic?.title || `选题 #${draft.topic_id}`} / 发布包 #${draft.id}`;
  };

  const selectedLogSummary = useMemo(() => {
    const latestMetric=latestMetricSnapshot(metrics);
    if(!latestMetric)return null;
    return {
      ...latestMetric,
      follow_conversion_rate: latestMetric.follow_conversion_rate ??
        rate(latestMetric.new_followers, latestMetric.views),
    };
  }, [metrics]);

  const handleDraftChange = (draftId: number) => {
    setSelectedLogId(null);setError('');setMessage('');
    const draft = draftById.get(draftId);
    setLogForm(prev => ({
      ...prev,
      draft_id: draftId,
      post_url:'',notes:'',
      used_title: draft?.title_options?.[0] || '',
      used_cover_text: draft?.cover_text_options?.[0] || '',
    }));
    setPredictionForm(prev => ({
      ...prev,
      draft_id: draftId,
      platform: logForm.platform,
      publish_log_id: null,
    }));
  };
  const startNewPrediction=()=>{
    const next=drafts.find(draft=>!logs.some(log=>log.draft_id===draft.id&&log.platform===logForm.platform));
    handleDraftChange(next?.id||0);setPredictionForm({...emptyPrediction,draft_id:next?.id||0,platform:logForm.platform});
    setPredictionReload(value=>value+1);
    setMessage(next?'已切回发布前预测。先保存预测，再创建发布记录。':'当前平台没有未发布稿件。请先准备新稿件，或选择另一个平台。');
  };

  const handleCreateLog = async () => {
    if (!logForm.draft_id) {
      alert('请先选择一个发布包');
      return;
    }
    setLoading(true);
    try {
      const created = await api.createPublishLog({
        ...logForm,
        published_at: logForm.published_at ? new Date(logForm.published_at).toISOString() : null,
      });
      setSelectedLogId(created.id);
      await load();
    } catch (e: any) {
      alert('创建发布记录失败: ' + e.message);
    }
    setLoading(false);
  };

  const handleCreateMetric = async () => {
    if (!selectedLogId) {
      alert('请先选择一条发布记录');
      return;
    }
    setLoading(true);
    try {
      await api.createMetric(selectedLogId, metricForm);
      setMetricForm(emptyMetric);
      setMetrics(await api.listMetrics(selectedLogId));
      setPredictions(await api.listPredictions({ publish_log_id: selectedLogId }));
    } catch (e: any) {
      alert('录入数据失败: ' + e.message);
    }
    setLoading(false);
  };

  const handleCreatePrediction = async () => {
    if(predictionDisabled){setError('预测需在创建发布记录前保存；已锁定或已结算预测不能修改。');return;}
    const draftId = logForm.draft_id;
    if (!draftId) {
      alert('请先选择发布包或发布记录');
      return;
    }
    setLoading(true);
    try {
      const payload: ContentPredictionCreate = {
        ...predictionForm,
        draft_id: draftId,
        publish_log_id: null,
        platform: logForm.platform,
        predicted_save_rate:null,predicted_like_rate:null,predicted_comment_rate:null,predicted_follow_conversion_rate:null,
      };
      const saved=latestPrediction?.status==='draft'?await api.updatePrediction(latestPrediction.id,payload):await api.createPrediction(payload);
      setPredictions(previous=>[saved,...previous.filter(item=>item.id!==saved.id)]);setMessage('发布前预测已保存。创建发布记录后会自动锁定，后续不能修改。');setError('');
    } catch (e: any) {
      alert('保存预测失败: ' + e.message);
    }
    setLoading(false);
  };

  const predictionSummary = useMemo(() => {
    const views = numberValue(predictionForm.predicted_views);
    return {
      saveRate: rate(numberValue(predictionForm.predicted_favorites),views),
      likeRate: rate(numberValue(predictionForm.predicted_likes),views),
      commentRate: rate(numberValue(predictionForm.predicted_comments),views),
      followRate: rate(numberValue(predictionForm.predicted_new_followers),views),
    };
  }, [predictionForm]);

  return (
    <div>
      <div className="page-header">
        <h1>数据录入</h1>
        <p>先保存发布前预测，再记录实际发布；定期录入截至目前的累计数据用于复盘。</p>
      </div>

      {error&&<div className="feedback error" role="alert">{error}</div>}{message&&<div className="feedback success" role="status">{message}</div>}
      <div className="grid-two">
        <section className="panel">
          <h3>创建发布记录</h3><p className="subtle">预测需先在下方保存。创建记录会锁定对应预测，发布后不能补写或改写事前判断。</p>
          <div className="form-group">
            <label>发布包</label>
            <select value={logForm.draft_id} onChange={e => handleDraftChange(Number(e.target.value))}>
              <option value={0}>请选择发布包</option>
              {drafts.map(draft => <option key={draft.id} value={draft.id}>{draftLabel(draft)}</option>)}
            </select>
          </div>
          <div className="form-row">
            <div className="form-group">
              <label>平台</label>
              <select disabled={!canWrite} value={logForm.platform} onChange={e => {setSelectedLogId(null);setLogForm({ ...logForm, platform: e.target.value });}}>
                <option value="xiaohongshu">小红书</option>
                <option value="douyin">抖音</option>
              </select>
            </div>
            <div className="form-group">
              <label>实际发布时间（留空按登记时间）</label>
              <input disabled={!canWrite} type="datetime-local" value={logForm.published_at} onChange={e => setLogForm({ ...logForm, published_at: e.target.value })} />
            </div>
          </div>
          <div className="form-group">
            <label>笔记链接</label>
            <input disabled={!canWrite} value={logForm.post_url} onChange={e => setLogForm({ ...logForm, post_url: e.target.value })} placeholder="https://..." />
          </div>
          <div className="form-group">
            <label>使用标题</label>
            <input disabled={!canWrite} value={logForm.used_title} onChange={e => setLogForm({ ...logForm, used_title: e.target.value })} />
          </div>
          <div className="form-group">
            <label>使用封面文案</label>
            <input disabled={!canWrite} value={logForm.used_cover_text} onChange={e => setLogForm({ ...logForm, used_cover_text: e.target.value })} />
          </div>
          <div className="form-group">
            <label>备注</label>
            <textarea disabled={!canWrite} value={logForm.notes} onChange={e => setLogForm({ ...logForm, notes: e.target.value })} />
          </div>
          <button className="btn btn-primary" onClick={handleCreateLog} disabled={!canWrite || (loading || !dataLoaded || !selectedDraft)}>
            创建发布记录
          </button>
        </section>

        <section className="panel">
          <div className="section-heading"><h3>发布记录</h3><button className="btn btn-sm" disabled={loading||!dataLoaded} onClick={startNewPrediction}>切回新预测</button></div>
          {logs.length === 0 ? (
            <div className="empty">暂无发布记录</div>
          ) : (
            <div className="stack-list">
              {logs.map(log => {
                const draft = draftById.get(log.draft_id);
                return (
                  <button
                    key={log.id}
                    className={`list-button ${selectedLogId === log.id ? 'active' : ''}`}
                    disabled={loading} onClick={() => {setSelectedLogId(log.id);setError('');setMessage('');}}
                  >
                    <strong>{log.used_title || draft?.title_options?.[0] || `发布记录 #${log.id}`}</strong>
                    <span>{PLATFORM_LABELS[log.platform] || log.platform} · {log.published_at ? new Date(log.published_at).toLocaleString('zh-CN') : '未填发布时间'}</span>
                  </button>
                );
              })}
            </div>
          )}
        </section>
      </div>

      <section className="panel prediction-panel" style={{ marginTop: 20 }}>
        <div className="panel-heading-row">
          <div>
            <h3>发布前预测</h3>
            <p>发布前先写下判断，后续录入真实数据时自动进入校准复盘。</p>
          </div>
          {latestPrediction && (
            <span className={`prediction-status prediction-status--${{draft:'发布前草稿',locked:'已锁定',settled:'已结算'}[latestPrediction.status]||'状态待确认'}`}>
              {{draft:'发布前草稿',locked:'已锁定',settled:'已结算'}[latestPrediction.status]||'状态待确认'}
            </span>
          )}
        </div>

        <p className="subtle">当前目标：{selectedLog?`发布记录 #${selectedLog.id} / 发布包 #${selectedLog.draft_id}`:logForm.draft_id?`未关联发布记录 / 发布包 #${logForm.draft_id}`:'尚未选择发布包'} · {PLATFORM_LABELS[selectedLog?.platform||logForm.platform]||logForm.platform}</p>
        {predictionLocked&&<div className="notice-strip">预测需在创建发布记录前保存。已锁定或已结算的预测只供查看。<span>要创建新的预测，请选择尚未在该平台发布的稿件。</span><button className="btn btn-sm" disabled={loading} onClick={startNewPrediction}>切回新预测</button></div>}
        {predictionLoading&&<p className="subtle" role="status">正在读取该稿件与平台的预测…</p>}
        {predictionReadError&&<button className="btn btn-sm" onClick={()=>setPredictionReload(value=>value+1)}>重新读取预测</button>}
        <fieldset className="readonly-page" disabled={predictionDisabled}>
        <div className="metric-form-grid prediction-grid">
          {[
            ['predicted_views', '预测浏览'],
            ['predicted_likes', '预测点赞'],
            ['predicted_favorites', '预测收藏'],
            ['predicted_comments', '预测评论'],
            ['predicted_shares', '预测分享'],
            ['predicted_new_followers', '预测涨粉'],
            ['confidence', '置信度'],
          ].map(([key, label]) => (
            <div className="form-group" key={key}>
              <label>{label}</label>
              <input disabled={!canWrite}
                type="number"
                min={0}
                max={key === 'confidence' ? 100 : undefined}
                value={(predictionForm as any)[key] ?? 0}
                onChange={e => setPredictionForm({ ...predictionForm, [key]: Number(e.target.value) })}
              />
            </div>
          ))}
          <div className="form-group">
            <label>规则版本</label>
            <input disabled={!canWrite} value={predictionForm.rubric_version || ''} onChange={e => setPredictionForm({ ...predictionForm, rubric_version: e.target.value })} />
          </div>
        </div>

        <div className="metric-summary prediction-summary">
          <h4>预测关键率</h4>
          <div className="metric-summary-grid">
            <span>收藏率：{ratePercent(predictionSummary.saveRate)}</span>
            <span>点赞率：{ratePercent(predictionSummary.likeRate)}</span>
            <span>评论率：{ratePercent(predictionSummary.commentRate)}</span>
            <span>关注转化：{ratePercent(predictionSummary.followRate)}</span>
          </div>
        </div>

        <div className="form-row" style={{ marginTop: 12 }}>
          <div className="form-group">
            <label>预测理由</label>
            <textarea disabled={!canWrite} value={predictionForm.rationale || ''} onChange={e => setPredictionForm({ ...predictionForm, rationale: e.target.value })} placeholder="为什么判断这条会高收藏/低评论/适合当前账号？" />
          </div>
          <div className="form-group">
            <label>不确定因素</label>
            <textarea disabled={!canWrite} value={predictionForm.risk_notes || ''} onChange={e => setPredictionForm({ ...predictionForm, risk_notes: e.target.value })} placeholder="比如发布时间、标题风险、封面不确定、热点窗口等。" />
          </div>
        </div>

        <div className="form-actions">
          <button className="btn btn-primary" onClick={handleCreatePrediction} disabled={predictionDisabled}>
            {latestPrediction?.status==='draft'?'更新发布前预测':'保存发布前预测'}
          </button>
        </div>

        </fieldset>
        {latestPrediction && (
          <div className="prediction-latest">
            <strong>最近预测 #{latestPrediction.id}</strong>
            <span>浏览 {latestPrediction.predicted_views} · 收藏 {latestPrediction.predicted_favorites} · 评论 {latestPrediction.predicted_comments} · 置信度 {latestPrediction.confidence}% · {new Date(latestPrediction.created_at).toLocaleString('zh-CN')}</span>
          </div>
        )}
      </section>

      <section className="panel" style={{ marginTop: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', marginBottom: 12 }}>
          <h3>录入累计平台数据</h3>
          {selectedLog && <span className="muted">当前记录 #{selectedLog.id}</span>}
        </div>
<div className="notice-strip">录入截至目前的累计值，不是相对上一次新增的数量。<span>例如上次浏览 100、本次累计浏览 150，此次填写 150。复盘只采用最新采集时间的快照；时间相同时采用编号较新的记录。</span></div>
          <div className="metric-form-grid">
          {[
            ['views', '浏览/播放'],
            ['likes', '点赞'],
            ['favorites', '收藏'],
            ['comments', '评论'],
            ['shares', '分享'],
            ['new_followers', '新增粉丝'],
            ['impressions', '曝光'],
            ['profile_visits', '主页访问'],
          ].map(([key, label]) => (
            <div className="form-group" key={key}>
              <label>{label}</label>
              <input disabled={!canWrite}
                type="number"
                min={0}
                value={(metricForm as any)[key] ?? ''}
                onChange={e => setMetricForm({ ...metricForm, [key]: e.target.value === '' ? null : Number(e.target.value) })}
              />
            </div>
          ))}
          <div className="form-group">
            <label>点击率</label>
            <input disabled={!canWrite} type="number" step="0.0001" value={metricForm.click_rate ?? ''} onChange={e => setMetricForm({ ...metricForm, click_rate: e.target.value === '' ? null : Number(e.target.value) })} />
          </div>
          <div className="form-group">
            <label>关注转化率</label>
            <input disabled={!canWrite} type="number" step="0.0001" value={metricForm.follow_conversion_rate ?? ''} onChange={e => setMetricForm({ ...metricForm, follow_conversion_rate: e.target.value === '' ? null : Number(e.target.value) })} />
          </div>
        </div>
        <div className="form-group">
          <label>数据备注</label>
          <textarea disabled={!canWrite} value={metricForm.notes || ''} onChange={e => setMetricForm({ ...metricForm, notes: e.target.value })} />
        </div>
          <button className="btn btn-primary" onClick={handleCreateMetric} disabled={!canWrite || (loading || !selectedLogId)}>保存本次数据</button>

          {selectedLogSummary && (
            <div className="metric-summary" style={{ marginTop: 16 }}>
              <h4>最新累计快照的关键率</h4><p className="subtle">采集时间：{new Date(selectedLogSummary.collected_at).toLocaleString('zh-CN')} · 快照 #{selectedLogSummary.id} · 浏览 {selectedLogSummary.views} · 收藏 {selectedLogSummary.favorites}。历史快照不会相加。</p>
              <div className="metric-summary-grid">
                <span>收藏率：{pct(selectedLogSummary.favorites, selectedLogSummary.views)}</span>
                <span>点赞率：{pct(selectedLogSummary.likes, selectedLogSummary.views)}</span>
                <span>评论率：{pct(selectedLogSummary.comments, selectedLogSummary.views)}</span>
                <span>关注转化率：{selectedLogSummary.follow_conversion_rate != null ? `${(selectedLogSummary.follow_conversion_rate * 100).toFixed(1)}%` : pct(selectedLogSummary.new_followers, selectedLogSummary.views)}</span>
              </div>
            </div>
          )}

        <h3 style={{ marginTop: 24, marginBottom: 12 }}>历史累计快照</h3>
        {metrics.length === 0 ? (
          <div className="empty">{selectedLogId?'该发布记录尚未采集指标，不按零表现计入复盘。':'请选择一条发布记录，查看或录入累计数据。'}</div>
        ) : (
              <table>
                <thead>
                  <tr>
                    <th>时间</th><th>浏览</th><th>点赞</th><th>收藏</th><th>评论</th><th>分享</th><th>新增粉丝</th><th>关键率（收藏/点赞/评论/关注）</th>
                  </tr>
                </thead>
                <tbody>
                  {metrics.map(metric => (
                    <tr key={metric.id}>
                      <td>{new Date(metric.collected_at).toLocaleString('zh-CN')}</td>
                      <td>{metric.views}</td>
                      <td>{metric.likes}</td>
                      <td>{metric.favorites}</td>
                      <td>{metric.comments}</td>
                      <td>{metric.shares}</td>
                      <td>{metric.new_followers}</td>
                      <td>
                        {pct(metric.favorites, metric.views)} / {pct(metric.likes, metric.views)} / {pct(metric.comments, metric.views)} / {(metric.follow_conversion_rate != null ? `${(metric.follow_conversion_rate * 100).toFixed(1)}%` : pct(metric.new_followers, metric.views))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
      </section>
    </div>
  );
}
