import React, { useCallback, useEffect, useState } from 'react';
import { api, setUnauthorizedHandler, subscribeEvents } from './api.js';
import { useApi } from './hooks.js';
import Icon from './components/icons.jsx';
import Login from './pages/Login.jsx';
import Overview from './pages/Overview.jsx';
import RunDetail from './pages/RunDetail.jsx';
import Settings from './pages/Settings.jsx';
import SpecList from './pages/SpecList.jsx';
import SpecDetail from './pages/SpecDetail.jsx';
import SpecEditor from './pages/SpecEditor.jsx';
import DesignList from './pages/DesignList.jsx';
import DesignSession from './pages/DesignSession.jsx';
import Approvals from './pages/Approvals.jsx';
import Usage from './pages/Usage.jsx';
import Schedules from './pages/Schedules.jsx';
import Runs from './pages/Runs.jsx';
import Compare from './pages/Compare.jsx';
import Audit from './pages/Audit.jsx';
import Nodes from './pages/Nodes.jsx';

// 해시 라우팅(#/runs/<id>) — 정적 파일 서빙만으로 새로고침/딥링크(텔레그램 링크 등)가 동작합니다.
const parseRoute = () => {
  const h = window.location.hash;
  for (const [prefix, page] of [['#/settings', 'settings'], ['#/approvals', 'approvals'], ['#/usage', 'usage'], ['#/schedules', 'schedules'], ['#/audit', 'audit'], ['#/nodes', 'nodes']]) {
    if (h.startsWith(prefix)) return { page };
  }
  if (h === '#/runs' || h === '#/runs/') return { page: 'runs' };
  const cmp = h.match(/^#\/compare\/(.+)$/);
  if (cmp) return { page: 'compare', ids: cmp[1].split(',').map(decodeURIComponent) };
  const ds = h.match(/^#\/design\/(\d+)/);
  if (ds) return { page: 'design-session', id: Number(ds[1]) };
  if (h.startsWith('#/design')) return { page: 'design' };
  if (h === '#/specs/new') return { page: 'spec-edit' };
  const e = h.match(/^#\/specs\/([^/]+)\/edit\/(\d+)/);
  if (e) return { page: 'spec-edit', name: decodeURIComponent(e[1]), version: Number(e[2]) };
  const sp = h.match(/^#\/specs\/([^/]+)(?:\/v\/(\d+))?/);
  if (sp) return { page: 'spec', name: decodeURIComponent(sp[1]), version: sp[2] ? Number(sp[2]) : null };
  if (h.startsWith('#/specs')) return { page: 'specs' };
  const m = h.match(/^#\/runs\/([^/]+)(?:\/iter\/(\d+))?/);
  return m ? { page: 'run', runId: decodeURIComponent(m[1]), iteration: m[2] ? Number(m[2]) : null } : { page: 'overview' };
};

// 메뉴 — 모바일 하단 탭에는 primary만, 나머지는 '더보기'에 둡니다.
const NAV = [
  { key: 'overview', href: '#/', icon: 'home', label: '홈', match: ['overview'], primary: true },
  { key: 'approvals', href: '#/approvals', icon: 'inbox', label: '승인', match: ['approvals'], primary: true },
  { key: 'usage', href: '#/usage', icon: 'gauge', label: '사용량', match: ['usage'], primary: true },
  { key: 'specs', href: '#/specs', icon: 'file', label: 'Spec', match: ['specs', 'spec', 'spec-edit'], primary: true },
  { key: 'runs', href: '#/runs', icon: 'bolt', label: '런', match: ['runs', 'run', 'compare'] },
  { key: 'design', href: '#/design', icon: 'chat', label: '설계', match: ['design', 'design-session'] },
  { key: 'schedules', href: '#/schedules', icon: 'calendar', label: '일정', match: ['schedules'] },
  { key: 'nodes', href: '#/nodes', icon: 'plug', label: '노드', match: ['nodes'] },
  { key: 'audit', href: '#/audit', icon: 'shield', label: '감사 로그', match: ['audit'] },
  { key: 'settings', href: '#/settings', icon: 'settings', label: '설정', match: ['settings'] },
];

export default function App() {
  const [auth, setAuth] = useState(null); // null = 확인 중
  const [route, setRoute] = useState(parseRoute);
  const [lastEvent, setLastEvent] = useState(null);
  const [streamStatus, setStreamStatus] = useState('connecting');
  const [moreOpen, setMoreOpen] = useState(false);

  const refreshAuth = useCallback(() => api('/auth/status').then(setAuth).catch(() => setAuth({ authenticated: false })), []);

  useEffect(() => {
    refreshAuth();
    setUnauthorizedHandler(() => setAuth((a) => ({ ...(a || {}), authenticated: false })));
    const onHash = () => { setRoute(parseRoute()); setMoreOpen(false); window.scrollTo(0, 0); };
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, [refreshAuth]);

  // 로그인 상태일 때만 실시간 이벤트 구독 — 페이지들은 lastEvent가 바뀌면 필요한 데이터만 다시 불러옵니다.
  useEffect(() => {
    if (!auth?.authenticated) return undefined;
    return subscribeEvents(setLastEvent, setStreamStatus);
  }, [auth?.authenticated]);

  // 승인 대기 수 — 메뉴 배지용 (승인 요청·판정 이벤트가 오면 갱신)
  const { data: appr } = useApi(auth?.authenticated ? '/api/approvals' : null, {
    refreshKey: lastEvent, shouldRefresh: (ev) => ['approval_required', 'approval', 'run_end', 'run_aborted'].includes(ev?.type), pollMs: 30000,
  });
  const pending = appr?.pending?.length || 0;

  if (auth === null) return <div className="center-screen muted">불러오는 중…</div>;
  if (!auth.authenticated) return <Login status={auth} onDone={refreshAuth} />;

  const logout = async () => { await api('/auth/logout', { method: 'POST' }); refreshAuth(); };
  const isActive = (n) => n.match.includes(route.page);
  const badge = (n) => (n.key === 'approvals' && pending ? <span className="nav-badge">{pending}</span> : null);

  return (
    <div className="shell">
      <header className="topbar">
        <a className="brand" href="#/">
          <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true">
            <circle cx="16" cy="16" r="13" fill="none" stroke="var(--accent)" strokeWidth="3" />
            <path d="M16 6a10 10 0 1 1-9.5 7" fill="none" stroke="var(--good)" strokeWidth="3" strokeLinecap="round" />
          </svg>
          <span>RSI Loop</span>
        </a>
        <nav className="topnav">
          {NAV.map((n) => (
            <a key={n.key} href={n.href} className={isActive(n) ? 'active' : ''}><Icon name={n.icon} size={16} />{n.label}{badge(n)}</a>
          ))}
        </nav>
        <span className={`live-dot ${streamStatus}`} title={streamStatus === 'live' ? '실시간 연결됨' : '재연결 중'} />
        <button className="ghost icon-btn" onClick={logout} title="로그아웃" aria-label="로그아웃"><Icon name="logout" size={17} /></button>
      </header>

      <main className="content">
        {route.page === 'runs' ? <Runs lastEvent={lastEvent} />
          : route.page === 'compare' ? <Compare ids={route.ids} />
          : route.page === 'run' ? <RunDetail runId={route.runId} focusIteration={route.iteration} lastEvent={lastEvent} />
          : route.page === 'approvals' ? <Approvals lastEvent={lastEvent} />
          : route.page === 'usage' ? <Usage lastEvent={lastEvent} />
          : route.page === 'schedules' ? <Schedules />
          : route.page === 'audit' ? <Audit />
          : route.page === 'nodes' ? <Nodes />
          : route.page === 'settings' ? <Settings />
          : route.page === 'design' ? <DesignList />
          : route.page === 'design-session' ? <DesignSession id={route.id} />
          : route.page === 'specs' ? <SpecList />
          : route.page === 'spec' ? <SpecDetail name={route.name} version={route.version} lastEvent={lastEvent} />
          : route.page === 'spec-edit' ? <SpecEditor name={route.name} fromVersion={route.version} />
          : <Overview lastEvent={lastEvent} />}
      </main>

      {/* 모바일 하단 탭 바 */}
      <nav className="tabbar" aria-label="주 메뉴">
        {/* 넓은 화면에서는 모든 메뉴를 펼치고(tab-extra), 휴대폰에서는 주요 메뉴 + 더보기 */}
        {NAV.map((n) => (
          <a key={n.key} href={n.href} className={`${isActive(n) ? 'active' : ''} ${n.primary ? '' : 'tab-extra'}`}>
            <span className="tab-icon"><Icon name={n.icon} size={21} />{badge(n)}</span>{n.label}
          </a>
        ))}
        <button className={`tab-more ${NAV.some((n) => !n.primary && isActive(n)) ? 'active' : ''}`} onClick={() => setMoreOpen((v) => !v)}>
          <span className="tab-icon"><Icon name="more" size={21} /></span>더보기
        </button>
      </nav>
      {moreOpen && (
        <div className="sheet-backdrop" onClick={() => setMoreOpen(false)}>
          <div className="sheet" onClick={(e) => e.stopPropagation()}>
            {NAV.filter((n) => !n.primary).map((n) => (
              <a key={n.key} href={n.href} className={isActive(n) ? 'active' : ''}><Icon name={n.icon} size={20} />{n.label}</a>
            ))}
            <button onClick={logout}><Icon name="logout" size={20} />로그아웃</button>
          </div>
        </div>
      )}
    </div>
  );
}
