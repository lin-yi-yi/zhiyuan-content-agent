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
  return typeof value === 'number' ? value : 0;
}

function formatPercent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

function formatSignedNumber(value: number) {
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
  const [reports, setReports] = useState<WeeklyReport[]>([]);
  const [selectedReport, setSelectedReport] = useState<WeeklyReport | null>(null);
  const [startDate, setStartDate] = useState(defaultStartDate());
  const [endDate, setEndDate] = useState(toDateInput(new Date()));
  const [loading, setLoading] = useState(false);

  const load = async () => {
    const data = await api.listWeeklyReports();
    setReports(data);
    setSelectedReport(prev => prev || data[0] || null);
  };

  useEffect(() => { load().catch(() => {}); }, []);

  const handleCreateReport = async () => {
    setLoading(true);
    try {
      const report = await api.createWeeklyReport({ start_date: startDate, end_date: endDate });
      setSelectedReport(report);
      setReports([report, ...reports]);
    } catch (e: any) {
      alert('生成复盘失败: ' + e.message);
    }
    setLoading(false);
  };

  const bestItems = selectedReport?.best_topics?.items || [];
  const worstItems = selectedReport?.worst_topics?.items || [];
  const recommendations = selectedReport?.recommendations?.items || [];
  const performanceSummary = selectedReport?.performance_summary || null;
  const anglePerformance = selectedReport?.angle_performance?.items || [];
  const contentTypePerformance = selectedReport?.content_type_performance?.items || [];
  const templatePerformance = selectedReport?.template_performance?.items || [];

  const summaryRates = performanceSummary?.rates || null;
  const summaryTotals = performanceSummary?.totals || null;
  const predictionCalibration = selectedReport?.prediction_calibration || performanceSummary?.prediction_calibration || null;
  const calibrationCount = metricValue(predictionCalibration || {}, 'prediction_count');
  const topPredictionMisses = Array.isArray(predictionCalibration?.top_misses)
    ? predictionCalibration.top_misses as Array<Record<string, unknown>>
    : [];

  return (
    <div>
      <div className="page-header">
        <h1>7天复盘</h1>
        <p>根据手动录入数据复盘选题、标题、卡片和下周方向</p>
      </div>

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
          <button className="btn btn-primary" onClick={handleCreateReport} disabled={loading}>
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
                  <span>发布 {metricValue(summaryTotals || {}, 'posts')} 条，浏览 {metricValue(summaryTotals || {}, 'views')}，收藏 {metricValue(summaryTotals || {}, 'favorites')}，新增粉丝 {metricValue(summaryTotals || {}, 'new_followers')}</span>
                </div>
              </>
            ) : <div className="empty">暂无关键率</div>}
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
                    <strong>{formatPercent(metricValue(predictionCalibration, 'avg_abs_view_error_rate'))}</strong>
                  </div>
                  <div className="calibration-card">
                    <span>偏差方向</span>
                    <strong>{biasLabel(predictionCalibration.view_bias)}</strong>
                  </div>
                  <div className="calibration-card">
                    <span>低估 / 高估</span>
                    <strong>{metricValue(predictionCalibration, 'underestimated_count')} / {metricValue(predictionCalibration, 'overestimated_count')}</strong>
                  </div>
                </div>
                {topPredictionMisses.length > 0 && (
                  <div className="stack-list" style={{ marginTop: 12 }}>
                    {topPredictionMisses.map((item, index) => {
                      const error = asRecord(item.prediction_error);
                      return (
                        <div className="topic-rank" key={index}>
                          <strong>{String(item.title || '未命名选题')}</strong>
                          <span>
                            真实浏览 {metricValue(item, 'views')} · 浏览偏差 {formatSignedNumber(metricValue(error, 'views_error'))} · 误差 {formatPercent(Math.abs(metricValue(error, 'views_error_rate')))}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </>
            ) : (
              <div className="empty compact-empty">暂无发布前预测数据。后续先记录预测，再录入真实指标，复盘会自动校准。</div>
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
                      <span>收藏 {metricValue(item, 'favorites')} · 评论 {metricValue(item, 'comments')} · 收藏率 {formatPercent(metricValue(item, 'favorites') / Math.max(metricValue(item, 'views'), 1))}</span>
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
                      <span>收藏 {metricValue(item, 'favorites')} · 评论 {metricValue(item, 'comments')} · 收藏率 {formatPercent(metricValue(item, 'favorites') / Math.max(metricValue(item, 'views'), 1))}</span>
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
                      <span>发布 {metricValue(item, 'posts')} · 收藏率 {formatPercent(metricValue(item, 'favorites') / Math.max(metricValue(item, 'views'), 1))} · 评论率 {formatPercent(metricValue(item, 'comments') / Math.max(metricValue(item, 'views'), 1))}</span>
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
                      <span>发布 {metricValue(item, 'posts')} · 收藏 {metricValue(item, 'favorites')} · 互动 {metricValue(item, 'engagement')} · 收藏率 {formatPercent(metricValue(item, 'favorites') / Math.max(metricValue(item, 'views'), 1))}</span>
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
                    <span>发布 {metricValue(item, 'posts')} · 评论率 {formatPercent(metricValue(item, 'comments') / Math.max(metricValue(item, 'views'), 1))} · 关注转化 {formatPercent(metricValue(item, 'follow_conversion_rate'))}</span>
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
