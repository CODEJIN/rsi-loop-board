import React, { useEffect, useState } from 'react';
import { useApi } from '../hooks.js';
import { Badge, DecisionBadge, StatusBadge } from '../components/common.jsx';
import ScoreChart from '../components/ScoreChart.jsx';
import IterationDetail from '../components/IterationDetail.jsx';
import EventLog from '../components/EventLog.jsx';
import NoiseReport from '../components/NoiseReport.jsx';
import AllocationPanel from '../components/AllocationPanel.jsx';
import { RunControls } from '../components/Controls.jsx';
import Icon from '../components/icons.jsx';
import { DecisionDots, Stepper, UsageRing } from '../components/viz.jsx';
import { PHASE, fmtTime, fmtValue, gainPct } from '../labels.js';

export default function RunDetail({ runId, focusIteration, lastEvent }) {
  const { data, error } = useApi(`/api/runs/${encodeURIComponent(runId)}`, {
    refreshKey: lastEvent, shouldRefresh: (ev) => ev?.run === runId,
  });
  const { data: orchestrator } = useApi('/api/orchestrator', { pollMs: 15000 });
  const [selected, setSelected] = useState(focusIteration);
  useEffect(() => setSelected(focusIteration), [focusIteration]);

  if (error) return <p className="error">{error.message}</p>;
  if (!data) return <p className="muted">불러오는 중…</p>;
  const { run, iterations } = data;
  const metric = run.metric;
  const pct = gainPct(run.baseline_objective, run.best_objective, metric);
  const usageIt = [...iterations].reverse().find((i) => i.usage);
  const latestUsage = usageIt ? { windows: usageIt.usage, ts: usageIt.ended_at || usageIt.started_at } : null;
  const selectedIt = iterations.find((i) => i.iteration === selected);
  const select = (n) => { window.location.hash = `#/runs/${encodeURIComponent(runId)}/iter/${n}`; };
  const active = run.status === 'running' || run.status === 'paused';
  // 현재 역할 설정 = 시작 설정 + 변경 이력(사용자·자동)을 순서대로 적용한 결과
  const currentRoles = (() => {
    const roles = structuredClone(data.allocation?.roles || {});
    for (const c of data.allocation?.changes || []) {
      for (const ch of c.changes || []) {
        roles[ch.role] = roles[ch.role] || {};
        for (const [k, v] of Object.entries(ch)) if (k !== 'role' && Array.isArray(v)) roles[ch.role][k] = v[1];
      }
    }
    return roles;
  })();

  const series = iterations.map((i) => ({ i: i.iteration, v: i.objective, d: i.decision }));
  const currentIt = iterations.at(-1);

  return (
    <div className="stack">
      <a href="#/" className="muted small back-link"><Icon name="chevron" size={14} className="flip" /> 홈</a>
      <section className="card run-head">
        <div className="row-between wrap">
          <div className="run-head-title">
            <h1 className="run-title">{run.title || run.spec || run.id}</h1>
            <div className="muted small">
              {run.spec && <a href={`#/specs/${encodeURIComponent(run.spec)}/v/${run.spec_version}`}>{run.spec} v{run.spec_version}</a>}
              {' · '}{fmtTime(run.started_at)}{run.ended_at && ` → ${fmtTime(run.ended_at).slice(6)}`}
            </div>
          </div>
          <div className="row-gap">
            {run.dry_run && <Badge tone="neutral">측정만</Badge>}
            {run.judge && run.judge !== 'auto' && <Badge tone="info"><Icon name="user" size={12} /> {run.judge === 'human' ? '사람 판정' : '혼합 판정'}</Badge>}
            <StatusBadge status={run.status} />
          </div>
        </div>
        {run.current && (
          <div className="now-bar">
            <div className="row-between wrap">
              <span><span className="pulse" /> {run.current.iteration ? `반복 ${run.current.iteration} · ` : ''}{PHASE[run.current.phase]}</span>
              {run.current.last_tool && <span className="muted small ellipsis">{run.current.last_tool}</span>}
            </div>
            {currentIt && run.current.iteration ? <Stepper it={currentIt} /> : null}
          </div>
        )}
        <div className="hero-row">
          <div className="hero">
            <div className="stat-label">최고 기록</div>
            <div className={`hero-num ${pct > 0 ? 'good-text' : ''}`}>{fmtValue(run.best_objective, metric)}</div>
            <div className="small muted">기준 {fmtValue(run.baseline_objective, metric)}{pct ? <b className={pct > 0 ? 'good-text' : ''}> · {pct > 0 ? '개선' : '악화'} {Math.abs(pct)}%</b> : ''}</div>
          </div>
          <div className="hero-side">
            <div className="stat-label">반복 {run.iterations_done}/{run.planned_iterations ?? '?'} · 채택 {run.accepted}</div>
            <DecisionDots series={series} onSelect={select} selected={selected} />
            <div className="muted small">채택 기준: 개선폭 {run.noise_threshold_ms != null ? `> ${fmtValue(run.noise_threshold_ms, metric)}` : '없음'}</div>
          </div>
          {latestUsage && (
            <div className="hero-usage">
              <UsageRing label="5시간" value={latestUsage.windows?.five_hour} size={78} />
            </div>
          )}
        </div>
      </section>

      {active && <RunControls run={run} orchestrator={orchestrator} lastEvent={lastEvent} onShowIteration={select} currentRoles={currentRoles} />}
      {run.dry_run && <NoiseReport run={run} iterations={iterations} />}

      <section className="grid-main">
        <div className="card">
          <h2>점수 추이 <span className="muted small">{metric.name} · {metric.direction === 'minimize' ? '낮을수록' : '높을수록'} 좋음</span></h2>
          <ScoreChart iterations={iterations} baseline={run.baseline_objective} metric={metric} onSelect={select} />
        </div>
        {!run.dry_run ? <AllocationPanel allocation={data.allocation} iterations={iterations} /> : <div />}
      </section>

      <section className="grid-main">
        <div className="card">
          <h2>반복</h2>
          <div className="iter-list">
            {iterations.length === 0 && <p className="muted small">아직 반복이 없습니다.</p>}
            {iterations.map((it) => (
              <button key={it.iteration} className={`iter-item ${selected === it.iteration ? 'selected' : ''}`} onClick={() => select(it.iteration)}>
                <div className="row-between">
                  <span className="row-gap"><b>#{it.iteration}</b>
                    {it.decision ? <DecisionBadge decision={it.decision} />
                      : it.phase === 'interrupted' ? <Badge tone="warn">{PHASE.interrupted}</Badge>
                        : it.phase === 'approval' ? <Badge tone="info">{PHASE.approval}</Badge>
                          : <span className="muted small"><span className="pulse" />{PHASE[it.phase]}</span>}
                  </span>
                  <span className="small">
                    <b>{fmtValue(it.objective, metric)}</b>
                    {it.improvement != null && <span className={it.improvement > 0 ? 'good-text' : 'muted'}> {it.improvement > 0 ? '▼' : '▲'}{fmtValue(Math.abs(it.improvement), metric)}</span>}
                  </span>
                </div>
                {!run.dry_run && <Stepper it={it} compact />}
                <div className="iter-summary small muted">{it.assigned || it.summary || (run.dry_run ? '측정만' : '진행 중…')}</div>
              </button>
            ))}
          </div>
        </div>
        {selectedIt
          ? <IterationDetail runId={runId} it={selectedIt} metric={metric} onClose={() => { window.location.hash = `#/runs/${encodeURIComponent(runId)}`; }} />
          : <div className="card muted center-text empty-state"><Icon name="chevron" size={22} /><div>반복을 선택하면 상세가 표시됩니다</div></div>}
      </section>

      <details className="card log-details">
        <summary><Icon name="file" size={15} /> 원본 이벤트 로그</summary>
        <EventLog runId={runId} lastEvent={lastEvent} metric={metric} />
      </details>
    </div>
  );
}
