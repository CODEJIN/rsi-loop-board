import React from 'react';
import { useApi } from '../hooks.js';
import Icon from './icons.jsx';
import { DecisionBadge } from './common.jsx';
import { fmtTime, fmtValue } from '../labels.js';

// 홈 화면 보조 패널 — 노드 · 일정 · Spec · 최근 활동의 요약. 자세한 관리는 각 화면에서.
const DAYS = ['월', '화', '수', '목', '금', '토', '일'];

function PanelHead({ icon, title, href, extra }) {
  return (
    <div className="panel-head">
      <span className="row-gap"><Icon name={icon} size={16} /><b>{title}</b>{extra}</span>
      {href && <a className="panel-link" href={href} aria-label={`${title} 화면으로`}>전체 <Icon name="chevron" size={13} /></a>}
    </div>
  );
}

export function NodesPanel() {
  const { data } = useApi('/api/nodes', { pollMs: 30000 });
  const nodes = data?.nodes || [];
  const online = nodes.filter((n) => n.online).length;
  return (
    <section className="card panel">
      <PanelHead icon="plug" title="노드" href="#/nodes" extra={data && <span className="muted small">{online}/{nodes.length} 연결</span>} />
      {!data ? <p className="muted small">불러오는 중…</p> : (
        <ul className="mini-list">
          {nodes.map((n) => (
            <li key={n.id}>
              <span className={`node-dot ${n.online == null ? 'unknown' : n.online ? 'on' : 'off'}`} />
              <span className="mini-main ellipsis">{n.name}</span>
              <span className="mini-tags">
                {n.info?.gpu && <span className="chip-static gpu">{n.info.gpu.replace(/^NVIDIA GeForce /, '')}</span>}
                {n.info?.cpu && <span className="chip-static">{n.info.cpu}코어</span>}
                <span className="chip-static">{n.os === 'windows' ? 'Win' : n.info?.arch === 'aarch64' ? 'ARM' : 'Linux'}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function SchedulesPanel() {
  const { data } = useApi('/api/schedules', { pollMs: 60000 });
  const list = (data || []).filter((s) => s.enabled).sort((a, b) => (a.in_window ? -1 : 0) - (b.in_window ? -1 : 0) || (a.next_start || '').localeCompare(b.next_start || ''));
  return (
    <section className="card panel">
      <PanelHead icon="calendar" title="일정" href="#/schedules" />
      {!data ? <p className="muted small">불러오는 중…</p> : list.length === 0 ? (
        <p className="muted small">켜진 일정이 없습니다. <a href="#/schedules">일정 추가</a></p>
      ) : (
        <ul className="mini-list">
          {list.slice(0, 4).map((s) => (
            <li key={s.id}>
              <Icon name="clock" size={14} className={s.in_window ? 'good-text' : 'muted'} />
              <span className="mini-main">
                <span className="ellipsis">{s.name}</span>
                <span className="muted small">{s.days.map((d) => DAYS[d]).join('')} {s.start_time}~{s.end_time}</span>
              </span>
              <span className="small">{s.in_window ? <b className="good-text">진행 창</b> : <span className="muted">{s.next_start ? fmtTime(s.next_start) : '—'}</span>}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function SpecsPanel() {
  const { data } = useApi('/api/specs');
  return (
    <section className="card panel">
      <PanelHead icon="file" title="Spec" href="#/specs" />
      {!data ? <p className="muted small">불러오는 중…</p> : data.length === 0 ? (
        <p className="muted small">Spec이 없습니다. <a href="#/specs/new">새 Spec</a></p>
      ) : (
        <ul className="mini-list">
          {data.slice(0, 5).map((s) => (
            <li key={s.name}>
              <span className={`spec-dot ${s.metric?.noise_threshold == null ? 'warn' : ''}`} title={s.metric?.noise_threshold == null ? '채택 기준 미정' : '실행 가능'} />
              <a className="mini-main" href={`#/specs/${encodeURIComponent(s.name)}`}>
                <span className="ellipsis">{s.title || s.name}</span>
                <span className="muted small ellipsis">{s.metric?.name} {s.metric?.direction === 'minimize' ? '↓' : '↑'} · 런 {s.runs || 0}회</span>
              </a>
              <span className="muted small">v{s.version}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const ACT = {
  run_start: ['play', '런 시작'], run_end: ['check', '런 완료'], run_aborted: ['stop', '런 중단'], paused: ['pause', '일시정지'],
};
export function ActivityPanel({ recent, runs }) {
  const metricOf = (id) => runs.find((r) => r.id === id)?.metric;
  const titleOf = (id) => { const r = runs.find((x) => x.id === id); return r ? (r.title || r.spec) : id; };
  return (
    <section className="card panel">
      <PanelHead icon="bolt" title="최근 활동" />
      {recent.length === 0 ? <p className="muted small">아직 활동이 없습니다.</p> : (
        <ul className="mini-list activity">
          {recent.slice(0, 8).map((e) => (
            <li key={`${e.run}-${e.seq}`}>
              <span className="mono muted small act-time">{fmtTime(e.ts).slice(6)}</span>
              {e.type === 'judgment' ? (
                <a className="mini-main" href={`#/runs/${encodeURIComponent(e.run)}/iter/${e.iteration}`}>
                  <span className="row-gap"><DecisionBadge decision={e.decision} /><span className="small">#{e.iteration}</span>
                    {(e.improvement ?? e.improvement_ms) > 0 && <span className="good-text small">▼{fmtValue(e.improvement ?? e.improvement_ms, metricOf(e.run))}</span>}</span>
                  <span className="muted small ellipsis">{titleOf(e.run)}</span>
                </a>
              ) : (
                <a className="mini-main" href={`#/runs/${encodeURIComponent(e.run)}`}>
                  <span className="row-gap small"><Icon name={ACT[e.type]?.[0] || 'more'} size={13} />{ACT[e.type]?.[1] || e.type}</span>
                  <span className="muted small ellipsis">{titleOf(e.run)}</span>
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
