import { useEffect, useState } from 'react';
import { api, ArchitectureInfo, KnowledgeBase } from '../api/client';

export default function ArchitecturePage() {
  const [architecture, setArchitecture] = useState<ArchitectureInfo | null>(null);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([api.getArchitecture(), api.listKnowledgeBases()])
      .then(([arch, bases]) => {
        setArchitecture(arch);
        setKnowledgeBases(bases);
      })
      .catch((err) => setError(err.message || '架构信息加载失败'))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="empty">正在读取当前架构与检索配置...</div>;
  }

  if (error || !architecture) {
    return <div className="empty">{error || '暂无架构信息'}</div>;
  }

  return (
    <div>
      <div className="page-header">
        <h1>架构边界</h1>
        <p>{architecture.product_boundary.product} · {architecture.version}</p>
      </div>

      <section className="architecture-hero">
        <div>
          <span>产品定位</span>
          <strong>{architecture.product_boundary.primary_scenario}</strong>
          <p>{architecture.data_isolation.rule}</p>
        </div>
        <div className="architecture-status-grid">
          <div>
            <span>Workspace</span>
            <strong>#{architecture.data_isolation.default_workspace_id}</strong>
          </div>
          <div>
            <span>Knowledge Base</span>
            <strong>#{architecture.data_isolation.default_knowledge_base_id}</strong>
          </div>
          <div>
            <span>LangChain</span>
            <strong>{architecture.framework_status.langchain_available ? '可导入' : '使用备用切分'}</strong>
          </div>
          <div>
            <span>LangGraph</span>
            <strong>{architecture.framework_status.langgraph_available ? '可导入' : '依赖不可用'}</strong>
          </div>
        </div>
      </section>

      <section className="panel">
        <h3>框架使用与验证范围</h3>
        <div className="architecture-fact-list">
          <p>{architecture.framework_status.langchain_usage}</p>
          <p>{architecture.framework_status.langgraph_usage}</p>
          <p>这里展示代码使用方式、当前配置与已保存的索引信息。实际检索质量请查看评测结果，实际任务执行请查看运行记录。</p>
        </div>
      </section>

      <div className="architecture-grid">
        <section className="panel">
          <h3>能力范围</h3>
          <div className="boundary-list">
            {architecture.product_boundary.in_scope.map((item) => (
              <div key={item} className="boundary-item allowed">{item}</div>
            ))}
          </div>
        </section>

        <section className="panel">
          <h3>明确不做</h3>
          <div className="boundary-list">
            {architecture.product_boundary.out_of_scope.map((item) => (
              <div key={item} className="boundary-item denied">{item}</div>
            ))}
          </div>
        </section>
      </div>

      <section className="panel">
        <h3>能力白名单</h3>
        <div className="capability-grid">
          {architecture.capabilities.map((item) => (
            <div className="capability-item" key={item.name}>
              <div>
                <span>{item.category}</span>
                <strong>{item.label}</strong>
              </div>
              <p>{item.data_scope}</p>
              <small>{item.allowed_actions.join(' / ')} · {item.can_write ? '有限写入' : '只读'}</small>
              <ul>
                {item.limitations.map((limit) => <li key={limit}>{limit}</li>)}
              </ul>
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <h3>工具调用白名单</h3>
        <div className="tool-grid">
          {(architecture.tools || []).map((item) => (
            <div className={`tool-item ${item.writes ? 'write' : 'read'}`} key={item.name}>
              <div>
                <span>{item.category} · {item.capability}</span>
                <strong>{item.name}</strong>
              </div>
              <p>{item.description}</p>
              <small>{item.action} · {item.writes ? '有限写入' : '只读'} · {item.data_scope}</small>
              <ul>
                {item.boundary_rules.map((rule) => <li key={rule}>{rule}</li>)}
              </ul>
            </div>
          ))}
        </div>
      </section>

      <div className="architecture-grid">
        <section className="panel">
          <h3>本地数据范围</h3>
          <div className="architecture-fact-list">
            <p><strong>知识库与执行记录：</strong>{architecture.data_isolation.tables.join(' / ')}</p>
            <p><strong>素材与内容：</strong>{architecture.data_isolation.legacy_tables}</p>
          </div>
        </section>

        <section className="panel">
          <h3>工作流边界</h3>
          <div className="architecture-fact-list">
            <p>{architecture.workflow_boundary.current}</p>
            <p>{architecture.workflow_boundary.v04_extension}</p>
            <p>{architecture.workflow_boundary.async_boundary}</p>
          </div>
        </section>
      </div>

      {architecture.retrieval_strategy && (
        <section className="panel">
          <h3>检索策略</h3>
          <div className="architecture-fact-list">
            <p><strong>{architecture.retrieval_strategy.name}</strong> · {architecture.retrieval_strategy.scoring}</p>
            <p><strong>Embedding：</strong>{architecture.retrieval_strategy.embedding_provider} / {architecture.retrieval_strategy.embedding_model}</p>
            <p><strong>当前知识库已存向量维度：</strong>{architecture.retrieval_strategy.embedding_dim > 0
              ? `${architecture.retrieval_strategy.embedding_dim} 维`
              : '未记录或当前模式不使用向量'}</p>
            <p>{architecture.retrieval_strategy.limitation}</p>
          </div>
        </section>
      )}

      <section className="panel">
        <h3>知识库</h3>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>名称</th>
              <th>Workspace</th>
              <th>状态</th>
              <th>边界说明</th>
            </tr>
          </thead>
          <tbody>
            {knowledgeBases.map((item) => (
              <tr key={item.id}>
                <td>#{item.id}</td>
                <td>{item.name}</td>
                <td>#{item.workspace_id}</td>
                <td>{item.status}</td>
                <td>{item.boundary_notes || item.purpose || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
