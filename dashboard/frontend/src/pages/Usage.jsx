import React, { useState } from 'react';
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { useApi } from '../hooks.js';
import Icon from '../components/icons.jsx';
import { ShareBar, UsageRing } from '../components/viz.jsx';

const RANGES = [[24, '24시간'], [72, '3일'], [168, '7일']];
const ROLE_LABEL = { planner: '분석', implementer: '구현', reviewer: '검토' };
const tick = (ts, hours) => (hours <= 24 ? ts.slice(11, 16) : `${ts.slice(5, 10).replace('-', '/')} ${ts.slice(11, 13)}시`);
const tooltipStyle = { background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 };

export default function Usage({ lastEvent }) {
  const [hours, setHours] = useState(24);
  const { data, error } = useApi(`/api/usage?hours=${hours}`, { refreshKey: lastEvent, shouldRefresh: (ev) => ev?.type === 'usage', pollMs: 60000 });
  if (error) return <p className="error">{error.message}</p>;
  if (!data) return <p className="muted">불러오는 중…</p>;
  const cur = data.current;
  // 시간 축은 실제 시각(epoch ms) — 기록이 띄엄띄엄해도 간격이 왜곡되지 않게 합니다.
  const samples = data.samples.map((s) => ({ ts: s.ts, x: new Date(s.ts).getTime(), five: s.five_hour != null ? Math.round(s.five_hour * 100) : null, seven: s.seven_day != null ? Math.round(s.seven_day * 100) : null }));
  const runs = [...data.runs].filter((r) => r.util_pct != null).slice(0, 10).reverse()
    .map((r) => ({ name: (r.started_at || r.id).slice(5, 16).replace('T', ' '), title: r.title || r.id, util: r.util_pct, accepted: r.accepted, iterations: r.iterations, id: r.id }));
  const eff = data.efficiency;
  const roles = Object.entries(data.role_totals).map(([k, v]) => ({ label: ROLE_LABEL[k] || k, value: v, cls: `role-${k}`, sub: `${v}%p` }));

  return (
    <div className="stack">
      <div className="row-between wrap">
        <h1 className="run-title">사용량</h1>
        <div className="segmented">
          {RANGES.map(([h, l]) => <button key={h} className={hours === h ? 'active' : ''} onClick={() => setHours(h)}>{l}</button>)}
        </div>
      </div>

      <section className="tiles">
        <div className="card tile-usage">
          <UsageRing label="5시간" value={cur?.five_hour} reset={cur?.five_reset} />
          <UsageRing label="7일" value={cur?.seven_day} reset={cur?.seven_reset} />
        </div>
        <div className="card tile">
          <Icon name="trend" size={22} />
          <div className="tile-num">{eff.util_per_accept ?? '—'}<span className="tile-unit">%p</span></div>
          <div className="tile-label">채택 1건당 사용량</div>
        </div>
        <div className="card tile">
          <Icon name="bolt" size={22} />
          <div className="tile-num">{eff.util_per_iteration ?? '—'}<span className="tile-unit">%p</span></div>
          <div className="tile-label">반복 1회당 사용량</div>
        </div>
      </section>

      <section className="card">
        <h2>5시간 · 7일 창 사용률 <span className="muted small">(%) — 루프·설계 대화 실행 중에만 기록</span></h2>
        {samples.length < 2 ? <p className="muted small">이 기간에 기록이 부족합니다.</p> : (
          <div className="chart">
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={samples} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis dataKey="x" type="number" scale="time" domain={['dataMin', 'dataMax']} tickFormatter={(x) => tick(new Date(x - new Date().getTimezoneOffset() * 60000).toISOString(), hours)}
                  stroke="var(--muted)" fontSize={11} minTickGap={50} />
                <YAxis domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} stroke="var(--muted)" fontSize={11} unit="%" width={44} />
                <Tooltip contentStyle={tooltipStyle} labelFormatter={(x, p) => (p?.[0]?.payload?.ts || '').replace('T', ' ')} formatter={(v, n) => [`${v}%`, n === 'five' ? '5시간' : '7일']} />
                <Line type="stepAfter" dataKey="five" stroke="var(--series-1)" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />
                <Line type="stepAfter" dataKey="seven" stroke="var(--series-2)" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />
              </LineChart>
            </ResponsiveContainer>
            <div className="legend small muted"><span><i className="line s1" />5시간</span><span><i className="line s2" />7일</span></div>
          </div>
        )}
      </section>

      <section className="grid-2">
        <div className="card">
          <h2>런별 사용량 <span className="muted small">(%p, 최근 10개)</span></h2>
          {runs.length === 0 ? <p className="muted small">기록이 없습니다.</p> : (
            <ResponsiveContainer width="100%" height={Math.max(160, runs.length * 30)}>
              <BarChart data={runs} layout="vertical" margin={{ top: 0, right: 40, bottom: 0, left: 0 }} barCategoryGap={6}>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="name" width={86} stroke="var(--muted)" fontSize={11} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={tooltipStyle} labelFormatter={(n, p) => p?.[0]?.payload?.title || n}
                  formatter={(v, n, p) => [`${v}%p · 반복 ${p.payload.iterations} · 채택 ${p.payload.accepted}`, '사용량']} />
                <Bar dataKey="util" fill="var(--series-1)" radius={[0, 4, 4, 0]} isAnimationActive={false}
                  label={{ position: 'right', fill: 'var(--muted)', fontSize: 11, formatter: (v) => `${v}%p` }} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
        <div className="card">
          <h2>역할별 누적 사용량 <span className="muted small">(추정 · 본인 사용 포함)</span></h2>
          {roles.length === 0 ? <p className="muted small">역할 분리를 켠 런이 끝나면 표시됩니다.</p> : <ShareBar items={roles} />}
          <div className="block stats-row">
            <div><div className="stat-label">집계 런 사용량</div><div className="stat-value">{eff.util_pct}%p</div></div>
            <div><div className="stat-label">반복</div><div className="stat-value">{eff.iterations}</div></div>
            <div><div className="stat-label">채택</div><div className="stat-value">{eff.accepted}</div></div>
          </div>
        </div>
      </section>
    </div>
  );
}
