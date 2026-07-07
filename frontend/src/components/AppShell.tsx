import { useEffect, useState } from 'react';
import { api } from '../api/client';

interface Props {
  active: string;
  onNavigate: (page: string) => void;
  children: React.ReactNode;
}

export default function AppShell({ active, onNavigate, children }: Props) {
  const [backendOnline, setBackendOnline] = useState(false);

  useEffect(() => {
    api.health().then(() => setBackendOnline(true)).catch(() => setBackendOnline(false));
  }, []);

  const navItems = [
    { key: 'agent', label: 'Agent 工作台' },
    { key: 'sources', label: '素材库' },
    { key: 'topics', label: '选题池' },
    { key: 'drafts', label: '发布包编辑' },
    { key: 'metrics', label: '数据录入' },
    { key: 'reports', label: '7天复盘' },
    { key: 'settings', label: '模型设置' },
    { key: 'architecture', label: '架构边界' },
    { key: 'rag', label: 'RAG 实验' },
  ];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">AI 提效实验室</div>
        <nav className="sidebar-nav">
          {navItems.map(item => (
            <a key={item.key} className={`nav-item ${active === item.key ? 'active' : ''}`}
               onClick={(e) => { e.preventDefault(); onNavigate(item.key); }} href="#">
              {item.label}
            </a>
          ))}
        </nav>
      </aside>
      <main className="main">
        <div className="top-bar">
          <span>
            <span className={`status-dot ${backendOnline ? 'online' : 'offline'}`} />
            后端 {backendOnline ? '已连接' : '断开'}
          </span>
          <span>v0.5 data loop · Model Router</span>
        </div>
        {children}
      </main>
    </div>
  );
}
