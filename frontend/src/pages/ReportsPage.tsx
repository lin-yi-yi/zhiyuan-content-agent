import { useWorkspace } from '../components/WorkspaceContext';
import { useEffect, useState } from 'react';
import { api, WeeklyReport } from '../api/client';

function toDateInput(date: Date) {
  return date.toISOString().slice(0, 10);
}

function defaultStartDate() {
  const date = new Date();
  date.setDate(date.getDate() - 6);
  return toDateInput(date);
}

function metricValue(item: Record<string, unknown>, key: string) {
  const value = item[key];
  return typeof value === 'number'&&Number.isFinite(value) ? value : null;
}
function metricLabel(item:Record<string,unknown>,key:string){return metricValue(item,key)??'未采集';}
function metricRate(item:Record<string,unknown>,key:string){const value=metricValue(item,key),base=metricValue(item,'views');return value!==null&&base!==null&&base>0?value/base:null;}

function formatPercent(value: number|null,empty='暂无有效数据') {
  return value===null?empty:`${(value * 100).toFixed(1)}%`;
}

function formatSignedNumber(value: number|null) {
  if(value===null)return '未采集';
  if (value > 0) return `+${value}`;
  return String(value);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function biasLabel(value: unknown) {
  const labels: Record<string, string> = {
    underestimated: '整体低估实际表现',
    overestimated: '整体高估实际表现',
    balanced: '高低估基本均衡',
    none: '暂无可校准数据',
  };
  return labels[String(value || 'none')] || '暂无可校准数据';
}

export default function ReportsPage() {
  const {canWrite,canReview}=useWorkspace();
  const [reports, setReports] = useState<WeeklyReport[]>([]);
  const [selectedReport, setSelectedReport] = useState<WeeklyReport | null>(null);
  const [startDate, setStartDate] = useState(defaultStartDate());
  const [endDate, setEndDate] = useState(toDateInput(new Date()));
  const [loading, setLoading] = useState(false);
  const [error,setError]=useState('');

  const load = async () => {
    const data = await api.listWeeklyReports();
    setReports(data);
    setSelectedReport(prev => prev || data[0] || null);
  };

  useEffect(() => { load().catch(e => setError(`复盘列表读取失败：${e.message}`)); }, []);

  const handleCreateReport = async () => {
    setLoading(true);setError('');
    try {
      const report = await api.createWeeklyReport({ start_date: startDate, end_date: endDate });
      setSelectedReport(report);
      setReports([report, ...reports]);
    } catch (e: any) {
      setError('生成复盘失败: ' + e.message);
    }
    setLoading(false);
  };

  const coverage=selectedReport?.performance_summary?.data_coverage;
  const measured=Boolean(coverage&&coverage.measured_posts>0);
  const bestItems = measured?selectedReport?.best_topics?.items || []:[];
  const worstItems = measured?selectedReport?.worst_topics?.items || []:[];
  const recommendations = selectedReport?.recommendations?.items || [];
  const performanceSummary = selectedReport?.performance_summary || null;
  const anglePerformance = measured?selectedReport?.angle_performance?.items || []:[];
  const contentTypePerformance = measured?selectedReport?.content_type_performance?.items || []:[];
  const templatePerformance = measured?selectedReport?.template_performance?.items || []:[];

  const summaryRates = measured?performanceSummary?.rates || null:null;
  const summaryTotals = performanceSummary?.totals || null;
  const predictionCalibration = measured?selectedReport?.prediction_calibration || performanceSummary?.prediction_calibration || null:null;
  const calibrationCount = metricValue(predictionCalibration || {}, 'prediction_count')??0;
  const topPredictionMisses = Array.isArray(predictionCalibration?.top_misses)
    ? predictionCalibration.top_misses as Array<Record<string, unknown>>
    : [];

  return (
    <div>
      <div className="page-header">
        <h1>7天复盘</h1>
        <p>根据手动录入数据复盘选题、标题、卡片和下周方向</p>
      </div>
      {error&&<div className="feedback error" role="alert">{error}</div>}

      <div className="grid-two">
        <section className="panel">
          <h3>生成复盘</h3>
          <div className="form-row">
            <div className="form-group">
              <label>开始日期</label>
              <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} />
            </div>
            <div className="form-group">
              <label>结束日期</label>
              <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} />
            </div>
          </div>
          <button className="btn btn-primary" onClick={handleCreateReport} disabled={!canWrite || (loading)}>
            {loading ? '生成中...' : '生成本周期复盘'}
          </button>
        </section>

        <section className="panel">
          <h3>历史复盘</h3>
          {reports.length === 0 ? (
            <div className="empty">暂无复盘报告</div>
          ) : (
            <div className="stack-list">
              {reports.map(report => (
                <button
                  key={report.id}
                  className={`list-button ${selectedReport?.id === report.id ? 'active' : ''}`}
                  onClick={() => setSelectedReport(report)}
                >
                  <strong>{report.start_date} 至 {report.end_date}</strong>
                  <span>{new Date(report.created_at).toLocaleString('zh-CN')}</span>
                </button>
              ))}
            </div>
          )}
        </section>
      </div>

      {selectedReport ? (
        <div style={{ marginTop: 20 }}>
          {coverage?<div className="notice-strip"><strong>本周期发布 {coverage.published_posts} 条 · 已采集 {coverage.measured_posts} 条 · 缺失指标 {coverage.missing_metric_posts} 条</strong><span>每条发布记录只采用最新的累计快照；缺失指标不按零表现参与排名或预测误差。明确录入的零值仍属于已采集数据。</span>{coverage.missing_metric_log_ids.length>0&&<span>待补数据的发布记录：{coverage.missing_metric_log_ids.map(id=>`#${id}`).join('、')}</span>}</div>:<div className="notice-strip">这份历史报告没有数据覆盖记录，请重新生成本周期复盘。<span>旧报告的正文保留供查阅，排名、关键率和校准需按新口径重新计算。</span></div>}
          <section className="panel">
            <h3>复盘报告</h3>
            <div className="report-text">{selectedReport.report_text}</div>
          </section>

          <section className="panel" style={{ marginTop: 20 }}>
            <h3>周期关键率</h3>
            {summaryRates ? (
              <>
                <div className="grid-two">
                  <div className="topic-rank">
                    <strong>收藏率</strong>
                    <span>{formatPercent(metricValue(summaryRates, 'save_rate'))}</span>
                  </div>
                  <div className="topic-rank">
                    <strong>点赞率</strong>
                    <span>{formatPercent(metricValue(summaryRates, 'like_rate'))}</span>
                  </div>
                  <div className="topic-rank">
                    <strong>评论率</strong>
                    <span>{formatPercent(metricValue(summaryRates, 'comment_rate'))}</span>
                  </div>
                  <div className="topic-rank">
                    <strong>关注转化率</strong>
                    <span>{formatPercent(metricValue(summaryRates, 'follow_conversion_rate'))}</span>
                  </div>
                </div>
                <div className="topic-rank" style={{ marginTop: 12 }}>
                  <strong>周期汇总曝光</strong>
                  <span>发布 {metricLabel(summaryTotals || {}, 'posts')} 条；以下累计总数仅含已采集内容：浏览 {metricLabel(summaryTotals || {}, 'views')}，收藏 {metricLabel(summaryTotals || {}, 'favorites')}，新增粉丝 {metricLabel(summaryTotals || {}, 'new_followers')}</span>
                </div>
              </>
            ) : <div className="empty">{coverage?'尚无已采集指标，暂不能计算关键率。':'请重新生成报告，取得已采集和缺失数据口径。'}</div>}
          </section>

          <section className="panel calibration-panel" style={{ marginTop: 20 }}>
            <div className="panel-heading-row">
              <div>
                <h3>预测校准</h3>
                <p>对比发布前预测与真实数据，判断选题和封面判断是否需要校准。</p>
              </div>
            </div>
            {predictionCalibration && calibrationCount > 0 ? (
              <>
                <div className="calibration-grid">
                  <div className="calibration-card">
                    <span>可校准内容</span>
                    <strong>{calibrationCount}</strong>
                  </div>
                  <div className="calibration-card">
                    <span>平均浏览误差</span>
                    <strong>{formatPercent(metricValue(predictionCalibration, 'avg_abs_view_error_rate'),'暂无有效误差样本')}</strong>
                  </div>
                  <div className="calibration-card">
                    <span>偏差方向</span>
                    <strong>{biasLabel(predictionCalibration.view_bias)}</strong>
                  </div>
                  <div className="calibration-card">
                    <span>低估 / 高估</span>
                    <strong>{metricLabel(predictionCalibration, 'underestimated_count')} / {metricLabel(predictionCalibration, 'overestimated_count')}</strong>
                  </div>
                </div>
                <p className="subtle">相对误差有效样本 {metricLabel(predictionCalibration,'relative_error_sample_count')} 条；预测为零、不能计算相对误差 {metricLabel(predictionCalibration,'undefined_relative_error_count')} 条。绝对误差仍保留。</p>
                <p className="subtle">已采集但没有有效事前预测的内容：{metricLabel(predictionCalibration,'measured_without_baseline_count')} 条。仅使用发布前保存并关联的单一预测基准。</p>
                {topPredictionMisses.length > 0 && (
                  <div className="stack-list" style={{ marginTop: 12 }}>
                    {topPredictionMisses.map((item, index) => {
                      const error = asRecord(item.prediction_error);
                      return (
                        <div className="topic-rank" key={index}>
                          <strong>{String(item.title || '未命名选题')}</strong>
                          <span>
                            真实浏览 {metricLabel(item, 'views')} · 浏览偏差 {formatSignedNumber(metricValue(error, 'views_error'))} · 相对误差 {metricValue(error,'views_error_rate')===null?'不可计算（预测为零）':formatPercent(Math.abs(metricValue(error,'views_error_rate')!))}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </>
            ) : (
              <div className="empty compact-empty">{coverage?'暂无同时具备有效事前预测和已采集指标的内容。请先保存预测，再创建发布记录，最后采集实际指标。':'请重新生成报告后查看预测校准。'}</div>
            )}
          </section>

          <div className="grid-two" style={{ marginTop: 20 }}>
            <section className="panel">
              <h3>表现较好选题</h3>
              {bestItems.length === 0 ? <div className="empty">暂无数据</div> : (
                <div className="stack-list">
                  {bestItems.map((item, index) => (
                    <div className="topic-rank" key={index}>
                      <strong>{String(item.title || '未命名选题')}</strong>
                      <span>收藏 {metricLabel(item, 'favorites')} · 评论 {metricLabel(item, 'comments')} · 收藏率 {formatPercent(metricRate(item, 'favorites'))}</span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="panel">
              <h3>需要调整选题</h3>
              {worstItems.length === 0 ? <div className="empty">暂无数据</div> : (
                <div className="stack-list">
                  {worstItems.map((item, index) => (
                    <div className="topic-rank" key={index}>
                      <strong>{String(item.title || '未命名选题')}</strong>
                      <span>收藏 {metricLabel(item, 'favorites')} · 评论 {metricLabel(item, 'comments')} · 收藏率 {formatPercent(metricRate(item, 'favorites'))}</span>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>

          <div className="grid-two" style={{ marginTop: 20 }}>
            <section className="panel">
              <h3>按选题角度聚合</h3>
              {anglePerformance.length === 0 ? <div className="empty">暂无角度聚合数据</div> : (
                <div className="stack-list">
                  {anglePerformance.map((item, index) => (
                    <div className="topic-rank" key={index}>
                      <strong>{String(item.label || '未标注角度')}</strong>
                      <span>发布 {metricLabel(item, 'posts')} · 收藏率 {formatPercent(metricRate(item, 'favorites'))} · 评论率 {formatPercent(metricRate(item, 'comments'))}</span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="panel">
              <h3>按模板聚合</h3>
              {templatePerformance.length === 0 ? <div className="empty">暂无模板聚合数据</div> : (
                <div className="stack-list">
                  {templatePerformance.map((item, index) => (
                    <div className="topic-rank" key={index}>
                      <strong>{String(item.label || '未标注模板')}</strong>
                      <span>发布 {metricLabel(item, 'posts')} · 收藏 {metricLabel(item, 'favorites')} · 互动 {metricLabel(item, 'engagement')} · 收藏率 {formatPercent(metricRate(item, 'favorites'))}</span>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>

          <section className="panel" style={{ marginTop: 20 }}>
            <h3>按内容类型聚合</h3>
            {contentTypePerformance.length === 0 ? <div className="empty">暂无内容类型聚合数据</div> : (
              <div className="stack-list">
                {contentTypePerformance.map((item, index) => (
                  <div className="topic-rank" key={index}>
                    <strong>{String(item.label || '未标注类型')}</strong>
                    <span>发布 {metricLabel(item, 'posts')} · 评论率 {formatPercent(metricRate(item, 'comments'))} · 关注转化 {formatPercent(metricValue(item, 'follow_conversion_rate'))}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel" style={{ marginTop: 20 }}>
            <h3>下周建议</h3>
            {recommendations.length === 0 ? (
              <div className="empty">暂无建议</div>
            ) : (
              <ul className="recommendation-list">
                {recommendations.map((item, index) => <li key={index}>{item}</li>)}
              </ul>
            )}
          </section>
        </div>
      ) : (
        <div className="empty" style={{ marginTop: 20 }}>还没有复盘报告，先生成一个周期复盘</div>
      )}
    </div>
  );
}
