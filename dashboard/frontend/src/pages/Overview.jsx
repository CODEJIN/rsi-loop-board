import React, { useState } from 'react';
import { useApi } from '../hooks.js';
import { StatusBadge } from '../components/common.jsx';
import { StartRun } from '../components/Controls.jsx';
import Icon from '../components/icons.jsx';
import { DecisionDots, Sparkline, Stepper, UsageRing } from '../components/viz.jsx';
import { PHASE, fmtTime, fmtValue, gainPct } from '../labels.js';
import { ActivityPanel, NodesPanel, SchedulesPanel, SpecsPanel } from '../components/HomePanels.jsx';

export default function Overview({ lastEvent }) {
  const { data, error } = useApi('/api/overview', { refreshKey: lastEvent, pollMs: 15000 });
  const { data: active } = useApi(data?.active?.[0] ? `/api/runs/${encodeURIComponent(data.active[0].id)}` : null,
    { refreshKey: lastEvent, shouldRefresh: (ev) => ev?.run === data?.active?.[0]?.id });
  const [showStart, setShowStart] = useState(false);
  const [showAll, setShowAll] = useState(false);
  if (error) return <p className="error">{error.message}</p>;
  if (!data) return <p className="muted">불러오는 중…</p>;
  const u = data.usage;
  // 홈에는 최근 주요 런 5개만 — 전체 관리(필터·보관·삭제·비교)는 런 화면에서
  const runs = data.runs.filter((r) => showAll || !(r.dry_run || r.status === 'aborted')).slice(0, 5);
  const hidden = data.runs.filter((r) => r.dry_run || r.status === 'aborted').length;
  const orch = data.orchestrator;

  return (
    <div className="stack">
      {/* ---- 상단: 사용량 · 승인 · 오케스트레이터 */}
      <section className="tiles">
        <div className="card tile-usage">
          <UsageRing label="5시간" value={u?.windows?.five_hour} reset={u?.resets?.five_hour} />
          <UsageRing label="7일" value={u?.windows?.seven_day} reset={u?.resets?.seven_day} />
        </div>
        <a className={`card tile ${data.pending_approvals ? 'tile-alert' : ''}`} href="#/approvals">
          <Icon name="inbox" size={22} />
          <div className="tile-num">{data.pending_approvals}</div>
          <div className="tile-label">승인 대기</div>
        </a>
        <div className="card tile">
          <Icon name="plug" size={22} />
          <div className={`tile-num small-num ${orch.online ? 'good-text' : 'error'}`}>{orch.online ? '연결됨' : orch.heartbeat_at ? '끊김' : '미설치'}</div>
          <div className="tile-label">오케스트레이터</div>
        </div>
      </section>

      {/* 넓은 화면: 왼쪽 = 런, 오른쪽 = 노드·일정·Spec·활동 / 좁은 화면: 위아래로 이어짐 */}
      <div className="home-cols">
      <div className="stack home-main">

      {/* ---- 실행 중 */}
      {data.active.map((r) => {
        const it = active?.run?.id === r.id ? active.iterations.at(-1) : null;
        const done = r.iterations_done, total = r.planned_iterations || 1;
        return (
          <a key={r.id} className="card active-card" href={`#/runs/${encodeURIComponent(r.id)}`}>
            <div className="row-between">
              <div className="row-gap"><span className="pulse" /><b>{r.title || r.spec}</b></div>
              <StatusBadge status={r.status} />
            </div>
            <div className="progress"><div className="progress-fill" style={{ width: `${(done / total) * 100}%` }} /></div>
            <div className="row-between wrap">
              <span className="small muted">반복 {r.current?.iteration || done}/{total} · {PHASE[r.current?.phase] || '대기'}</span>
              {r.pending_approval && <span className="badge info"><Icon name="user" size={12} /> 승인 필요</span>}
            </div>
            {it && <Stepper it={it} />}
            <div className="row-between">
              <DecisionDots series={r.series} />
              <span className="small">최고 <b>{fmtValue(r.best_objective, r.metric)}</b></span>
            </div>
          </a>
        );
      })}

      {/* ---- 새 런 */}
      <section className="card">
        <button className="section-toggle" onClick={() => setShowStart((v) => !v)}>
          <span className="row-gap"><Icon name="play" size={16} /> 새 런 시작</span>
          <Icon name="chevron" size={16} className={showStart ? 'rot90' : ''} />
        </button>
        {showStart && <StartRun orchestrator={orch} usage={u} />}
      </section>

      {/* ---- 런 목록 */}
      <section className="card">
        <div className="row-between">
          <h2>최근 런</h2>
          <span className="row-gap">
            {hidden > 0 && <button className="ghost small" onClick={() => setShowAll((v) => !v)}>{showAll ? '주요 런만' : '측정만·중단 포함'}</button>}
            <a className="button-link small-link" href="#/runs">모든 런 <Icon name="chevron" size={13} /></a>
          </span>
        </div>
        {runs.length === 0 ? <p className="muted small">아직 런이 없습니다.</p> : (
          <div className="run-cards">
            {runs.map((r) => {
              const pct = gainPct(r.baseline_objective, r.best_objective, r.metric);
              return (
                <a key={r.id} className="run-row" href={`#/runs/${encodeURIComponent(r.id)}`}>
                  <div className="run-row-main">
                    <div className="row-gap"><b className="ellipsis">{r.title || r.spec || r.id}</b>{r.spec_version && <span className="muted small">v{r.spec_version}</span>}</div>
                    <div className="muted small">{fmtTime(r.started_at)}</div>
                  </div>
                  <Sparkline series={r.series} baseline={r.baseline_objective} metric={r.metric} />
                  <div className="run-row-num">
                    <b>{fmtValue(r.best_objective, r.metric)}</b>
                    {pct ? <span className={`small ${pct > 0 ? 'good-text' : 'muted'}`}>{pct > 0 ? '▼' : '▲'}{Math.abs(pct)}%</span> : <span className="small muted">—</span>}
                  </div>
                  <div className="run-row-dots"><DecisionDots series={r.series} /></div>
                  <StatusBadge status={r.status} />
                </a>
              );
            })}
          </div>
        )}
      </section>
      <ActivityPanel recent={data.recent} runs={data.runs} />
      </div>
      <aside className="stack home-side">
        <NodesPanel />
        <SchedulesPanel />
        <SpecsPanel />
      </aside>
      </div>
    </div>
  );
}
