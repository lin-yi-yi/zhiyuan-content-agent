import { useEffect, useState } from 'react';
import { request } from '../api/client';

interface Provider {
  provider: string;
  model: string;
  configured: boolean;
  base_url: string;
}

interface ModelRun {
  id: number;
  task_type: string;
  provider: string;
  model_name: string;
  success: boolean;
  latency_ms: number | null;
  prompt_hash: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  error_type: string | null;
  created_at: string | null;
}

interface TaskDefault {
  task: string;
  label: string;
  provider: string;
  model: string;
}

export default function SettingsPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [taskDefaults, setTaskDefaults] = useState<TaskDefault[]>([]);
  const [runs, setRuns] = useState<ModelRun[]>([]);
  const [testResult, setTestResult] = useState('');
  const [selectedProvider, setSelectedProvider] = useState('local');
  const [chatPrompt, setChatPrompt] = useState('用一句话介绍 AI Agent');
  const [chatResult, setChatResult] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');

  const load = async () => {
    try {
      const [providerData, taskData, runData] = await Promise.all([
        request<{providers: Provider[]}>('/api/models/providers'),
        request<{defaults: TaskDefault[]}>('/api/models/task-defaults'),
        request<{runs: ModelRun[]}>('/api/models/runs?limit=10'),
      ]);
      setProviders(providerData.providers);
      setTaskDefaults(taskData.defaults);
      setRuns(runData.runs);
      setLoadError('');
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : '暂时无法读取模型设置');
    }
  };

  useEffect(() => { load(); }, []);

  const testProvider = async (provider: string) => {
    setTestResult('测试中...');
    try {
      const d = await request<{ok: boolean; response: string}>(`/api/models/test/${provider}`, { method: 'POST' });
      setTestResult(`✅ ${provider} 连接成功: ${d.response}`);
    } catch(e: any) {
      setTestResult(`❌ ${e.message}`);
    } finally {
      void load();
    }
  };

  const testChat = async () => {
    setLoading(true);
    setChatResult('');
    try {
      const d = await request<{content: string}>('/api/models/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: selectedProvider, user_prompt: chatPrompt, max_tokens: 100 }),
      });
      setChatResult(d.content);
    } catch(e: any) {
      setChatResult(`错误: ${e.message}`);
    } finally {
      setLoading(false);
      void load();
    }
  };

  return (
    <div>
      <div className="page-header">
        <h1>⚙️ 模型设置</h1>
        <p>管理 LLM 供应商，查看调用记录</p>
      </div>
      {loadError && <p role="alert" style={{ color: 'var(--red)', marginBottom: 16 }}>{loadError}</p>}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        {/* 供应商列表 */}
        <div style={{ background: 'var(--surface)', borderRadius: 8, padding: 20, border: '1px solid var(--border)' }}>
          <h3 style={{ marginBottom: 12 }}>模型供应商</h3>
          <table>
            <thead><tr><th>供应商</th><th>模型</th><th>状态</th><th>测试</th></tr></thead>
            <tbody>
              {providers.map(p => (
                <tr key={p.provider}>
                  <td><strong>{p.provider}</strong></td>
                  <td style={{ fontSize: 12 }}>{p.model}</td>
                  <td><span className={`badge ${p.configured ? 'badge-published' : 'badge-discarded'}`}>
                    {p.configured ? '已配置' : '未配置'}</span></td>
                  <td>
                    <button className="btn btn-sm" onClick={() => testProvider(p.provider)}
                            disabled={!p.configured}>测试</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {testResult && <div style={{ marginTop: 12, fontSize: 13, color: testResult.includes('✅') ? 'var(--green)' : 'var(--red)' }}>{testResult}</div>}
        </div>

        {/* 对话测试 */}
        <div style={{ background: 'var(--surface)', borderRadius: 8, padding: 20, border: '1px solid var(--border)' }}>
          <h3 style={{ marginBottom: 12 }}>对话测试</h3>
          <div className="form-group">
            <label>供应商</label>
            <select value={selectedProvider} onChange={e => setSelectedProvider(e.target.value)}>
              {providers.map(p => (
                <option key={p.provider} value={p.provider} disabled={!p.configured}>
                  {p.provider} / {p.model}{p.configured ? '' : '（未配置）'}
                </option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <textarea value={chatPrompt} onChange={e => setChatPrompt(e.target.value)} rows={2}
                      style={{ width: '100%' }} />
          </div>
          <button className="btn btn-primary" onClick={testChat} disabled={loading || !chatPrompt.trim()}>
            {loading ? '调用中...' : '发送'}
          </button>
          {chatResult && (
            <div style={{ marginTop: 12, padding: 12, background: 'var(--bg)', borderRadius: 6, fontSize: 13, lineHeight: 1.6 }}>
              {chatResult}
            </div>
          )}
        </div>
      </div>

      <div style={{ marginTop: 24, background: 'var(--surface)', borderRadius: 8, padding: 20, border: '1px solid var(--border)' }}>
        <h3 style={{ marginBottom: 12 }}>Agent 任务默认模型</h3>
        <table>
          <thead><tr><th>任务</th><th>供应商</th><th>模型</th></tr></thead>
          <tbody>
            {taskDefaults.map(item => (
              <tr key={item.task}>
                <td>{item.label}</td>
                <td>{item.provider}</td>
                <td style={{ fontSize: 12 }}>{item.model}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 调用记录 */}
      <div style={{ marginTop: 24, background: 'var(--surface)', borderRadius: 8, padding: 20, border: '1px solid var(--border)' }}>
        <h3 style={{ marginBottom: 12 }}>最近调用记录</h3>
        <p style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 12 }}>
          记录仅展示调用元数据。输入和输出用量以服务商返回的 token 数为准；“—”表示未提供，本地规则模型不产生模型 token 用量。
        </p>
        <div style={{ overflowX: 'auto' }}>
        <table>
          <thead><tr><th>任务</th><th>供应商 / 模型</th><th>耗时</th><th>输入 / 输出 token</th><th>总 token</th><th>状态 / 错误类型</th><th>时间</th></tr></thead>
          <tbody>
            {runs.length === 0 ? (
              <tr><td colSpan={7} className="empty">暂无记录</td></tr>
            ) : runs.map(r => (
              <tr key={r.id}>
                <td>{r.task_type}</td>
                <td>{r.provider}<div style={{ fontSize: 12 }}>{r.model_name}</div></td>
                <td>{r.latency_ms == null ? '—' : `${r.latency_ms} ms`}</td>
                <td>{r.prompt_tokens?.toLocaleString() ?? '—'} / {r.completion_tokens?.toLocaleString() ?? '—'}</td>
                <td>{r.total_tokens?.toLocaleString() ?? '—'}</td>
                <td><span className={`badge ${r.success ? 'badge-published' : 'badge-discarded'}`}>
                  {r.success ? '成功' : '失败'}</span>
                  {r.error_type && <div style={{ fontSize: 12, marginTop: 4 }}>{r.error_type}</div>}
                </td>
                <td style={{ fontSize: 12 }}>{r.created_at ? new Date(r.created_at).toLocaleString('zh-CN') : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </div>
    </div>
  );
}
