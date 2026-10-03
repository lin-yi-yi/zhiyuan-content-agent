import { useEffect, useState } from 'react';
import { ROLE_LABELS } from '../api/saas';
import { useWorkspace } from './WorkspaceContext';
import '../styles/commercial-workspace.css';

interface Props { active: string; onNavigate: (page: string) => void; children: React.ReactNode }
const sections = [
  {key: 'overview', label: '工作台', icon: '◫', pages: ['overview']},
  {key: 'brands', label: '品牌与资料', icon: '▧', pages: ['brands', 'knowledge', 'source-hub', 'evidence', 'sources']},
  {key: 'agent', label: '内容任务', icon: '✧', pages: ['agent', 'rag', 'topics']},
  {key: 'drafts', label: '审核交付', icon: '▤', pages: ['drafts']},
  {key: 'reports', label: '效果复盘', icon: '◷', pages: ['reports', 'metrics', 'pilot']},
  {key: 'connections', label: '设置', icon: '⚙', pages: ['connections', 'settings', 'team', 'billing', 'account']},
];
const tabs: Record<string, string[][]> = {
  brands: [['brands', '品牌档案'], ['knowledge', '知识资料'], ['source-hub', '发现信源']],
  agent: [['agent', '创作任务'], ['rag', '资料问答']],
  reports: [['reports', '发布复盘'], ['metrics', '发布数据'], ['pilot', '试点验收']],
  connections: [['connections', '模型与信源'], ['settings', '运行诊断']],
};
const moreTabs: Record<string, string[][]> = {
  brands: [['evidence', '核验笔记'], ['sources', '原始素材']],
  agent: [['topics', '备选选题']],
};

export default function AppShell({active, onNavigate, children}: Props) {
  const {session, organization, switchOrganization, canWrite} = useWorkspace();
  const [menu, setMenu] = useState(false);
  const section = sections.find(item => item.pages.includes(active)) || sections[0];
  const isSaas = session.mode === 'saas';
  const secondary = section.key === 'connections' && isSaas
    ? [['connections', '模型与信源'], ['team', '团队成员'], ['billing', '用量'], ['account', '账号']]
    : tabs[section.key] || [];
  const more = moreTabs[section.key] || [];
  const moreActive = more.find(([page]) => page === active);
  useEffect(() => { setMenu(false); }, [active]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenu(false); };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, []);
  const navigate = (page: string) => { setMenu(false); onNavigate(page); };

  return <div className={`app-shell saas-shell calm-shell commercial-shell ${menu ? 'mobile-menu-open' : ''}`}>
    {menu && <button className="mobile-nav-backdrop" aria-label="收起导航" onClick={() => setMenu(false)}/>}
    <aside className="sidebar">
      <button className="sidebar-brand" onClick={() => navigate('overview')}><span className="brand-mark">知</span><span>知源<small>内容创作与交付工作台</small></span></button>
      <nav className="sidebar-nav" aria-label="主导航">{sections.slice(0, 5).map(item =>
        <a key={item.key} href={`#${item.key}`} className={`nav-item ${section.key === item.key ? 'active' : ''}`} aria-current={section.key === item.key ? 'page' : undefined} onClick={event => { event.preventDefault(); navigate(item.key); }}>
          <span className="nav-icon" aria-hidden="true">{item.icon}</span>{item.label}{section.key === item.key && <i/>}
        </a>
      )}</nav>
      <div className="sidebar-business-note"><span>每一次交付，都有据可查</span><p>统一品牌表达，保留资料来源，确认内容后再交付。</p><button onClick={() => navigate('brands')} className="text-action">管理品牌资料 <span aria-hidden="true">→</span></button></div>
      <button className={`sidebar-settings ${section.key === 'connections' ? 'active' : ''}`} onClick={() => navigate('connections')} aria-current={section.key === 'connections' ? 'page' : undefined}><span aria-hidden="true">⚙</span> 设置与连接</button>
      <div className="sidebar-bottom"><span className="workspace-avatar">{(session.user?.name || '本').slice(0, 1)}</span><div><strong>{session.user?.name || '本地工作空间'}</strong><small>{organization ? ROLE_LABELS[organization.role] : '无需登录'}</small></div></div>
    </aside>
    <main className="main">
      <header className="top-bar saas-topbar"><div className="topbar-location"><button className="btn mobile-nav-toggle" aria-expanded={menu} aria-label={menu ? '收起导航' : '展开导航'} onClick={() => setMenu(!menu)}>☰</button><span>工作空间</span><i>/</i><strong>{section.label}</strong></div><div className="topbar-tools">
        {isSaas ? <label className="org-switch"><span className="source-hub-sr-only">切换组织</span><select aria-label="当前组织" value={organization?.id || ''} onChange={event => switchOrganization(event.target.value)}>{session.organizations.map(org => <option key={org.id} value={org.id}>{org.name}</option>)}</select></label> : <span className="local-status"><i/> 本地 · 免登录</span>}
        <button className="btn btn-sm" disabled={!canWrite} onClick={() => navigate('agent')}>新建内容</button>
      </div></header>
      <div className="saas-content">
        {secondary.length > 0 && <div className="business-subnavigation"><nav className="workspace-tabs" aria-label={`${section.label}分类`}>{secondary.map(([page, label]) =>
          <a key={page} href={`#${page}`} aria-current={active === page ? 'page' : undefined} className={active === page ? 'active' : ''} onClick={event => { event.preventDefault(); navigate(page); }}>{label}</a>
        )}</nav>{more.length > 0 && <details className={`workspace-more ${moreActive ? 'active' : ''}`} key={active}><summary>{moreActive?.[1] || '更多'}</summary><div>{more.map(([page, label]) => <a key={page} href={`#${page}`} aria-current={active === page ? 'page' : undefined} onClick={event => { event.preventDefault(); navigate(page); }}>{label}</a>)}</div></details>}</div>}
        {isSaas && organization?.role === 'viewer' && <div className="role-notice">只读访问 · 可以查看团队资料并使用资料问答</div>}
        {children}
      </div>
    </main>
  </div>;
}
