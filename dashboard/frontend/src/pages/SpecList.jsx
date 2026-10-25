import React from 'react';
import { useApi } from '../hooks.js';
import { Badge } from '../components/common.jsx';
import { fmtTime, fmtValue } from '../labels.js';

const JUDGE = { auto: '자동', human: '사람', hybrid: '혼합' };

export default function SpecList() {
  const { data, error } = useApi('/api/specs');
  if (error) return <p className="error">{error.message}</p>;
  if (!data) return <p className="muted">불러오는 중…</p>;
  return (
    <div className="stack">
      <div className="row-between">
        <h1 className="run-title">Spec</h1>
        <a className="button-link primary" href="#/specs/new">+ 새 Spec</a>
      </div>
      <p className="muted small">Spec은 “무엇을 개선하고, 어떻게 평가하고, 누가 판정하는지”를 담은 정의서입니다. 저장할 때마다 새 버전이 쌓이고, 런은 특정 버전으로 실행됩니다.</p>
      {data.length === 0 && <div className="card muted">아직 Spec이 없습니다.</div>}
      <div className="spec-grid">
        {data.map((s) => (
          <a key={s.name} className="card spec-card" href={`#/specs/${encodeURIComponent(s.name)}`}>
            <div className="row-between">
              <b>{s.title || s.name}</b>
              <span className="muted small">v{s.version}</span>
            </div>
            <div className="muted small mono">{s.name}</div>
            <p className="small spec-goal">{s.goal}</p>
            <div className="row-gap wrap-gap small">
              <Badge tone="neutral">{s.metric?.name} · {s.metric?.direction === 'minimize' ? '↓' : '↑'}</Badge>
              <Badge tone={s.metric?.noise_threshold == null ? 'warn' : 'neutral'}>
                {s.metric?.noise_threshold == null ? '채택 기준 미정' : `기준 >${fmtValue(s.metric.noise_threshold, s.metric)}`}
              </Badge>
              <Badge tone="neutral">판정 {JUDGE[s.judge] || s.judge}</Badge>
            </div>
            <div className="muted small">런 {s.runs || 0}회{s.last_run && ` · 최근 ${fmtTime(s.last_run)}`}</div>
          </a>
        ))}
      </div>
    </div>
  );
}
