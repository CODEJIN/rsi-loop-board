import React from 'react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { useApi } from '../hooks.js';
import Icon from '../components/icons.jsx';
import { DecisionDots } from '../components/viz.jsx';
import { fmtTime, fmtValue, gainPct } from '../labels.js';

// 런 비교 — 최대 3개. 런마다 고정 색(분류 팔레트 1~3번, 색각이상 대비 검사 통과)을 쓰고, 이름을 항상 함께 표시합니다.
const SLOT = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)'];
const ROLE_SHORT = { planner: '분석', implementer: '구현', reviewer: '검토' };

export default function Compare({ ids }) {
  const { data, error } = useApi(`/api/compare?ids=${ids.map(encodeURIComponent).join(',')}`);
  if (error) return <p className="error">{error.message}</p>;
  if (!data) return <p className="muted">불러오는 중…</p>;
  if (data.length < 2) return <p className="muted">비교할 런이 2개 이상 필요합니다.</p>;
  const sameMetric = data.every((r) => r.metric?.name === data[0].metric?.name);
  const metric = data[0].metric;
  // 반복 번호를 x축으로, 런마다 '그 시점까지의 최고 기록'을 계단선으로 그립니다(0 = 기준선).
  const maxIt = Math.max(...data.map((r) => r.series.length));
  const rows = Array.from({ length: maxIt + 1 }, (_, i) => {
    const row = { i };
    data.forEach((r, k) => {
      if (i > r.series.length) return;
      let best = r.baseline_objective;
      for (const p of r.series.slice(0, i)) if (p.d === 'accepted' && p.v != null) best = p.v;
      row[`r${k}`] = best;
    });
    return row;
  });
  const label = (r) => `${r.title || r.spec} · ${fmtTime(r.started_at)}`;
  const cells = [
    ['기준선', (r) => fmtValue(r.baseline_objective, r.metric)],
    ['최고 기록', (r) => <b>{fmtValue(r.best_objective, r.metric)}</b>],
    ['개선율', (r) => { const p = gainPct(r.baseline_objective, r.best_objective, r.metric); return p ? <span className={p > 0 ? 'good-text' : ''}>{p > 0 ? '개선' : '악화'} {Math.abs(p)}%</span> : '—'; }],
    ['반복 · 채택', (r) => `${r.iterations_done} · ${r.accepted}`],
    ['사용량', (r) => (r.util_pct != null ? `${r.util_pct}%p` : '—')],
    ['채택 1건당', (r) => (r.util_pct != null && r.accepted ? `${(r.util_pct / r.accepted).toFixed(1)}%p` : '—')],
    ['소요 시간', (r) => `${r.durations_min}분`],
    ['역할', (r) => (r.roles ? Object.entries(r.roles).filter(([, v]) => v.enabled !== false && v.when !== 'off').map(([k]) => ROLE_SHORT[k] || k).join('·') : '구현만')],
    ['판정', (r) => <DecisionDots series={r.series} />],
  ];

  return (
    <div className="stack">
      <a href="#/runs" className="muted small back-link"><Icon name="chevron" size={14} className="flip" /> 런 목록</a>
      <h1 className="run-title">런 비교</h1>
      <div className="compare-legend">
        {data.map((r, k) => (
          <a key={r.id} href={`#/runs/${encodeURIComponent(r.id)}`} className="compare-chip"><i style={{ background: SLOT[k] }} />{label(r)}</a>
        ))}
      </div>

      <section className="card">
        <h2>최고 기록 추이 <span className="muted small">{sameMetric ? `${metric.name} · ${metric.direction === 'minimize' ? '낮을수록' : '높을수록'} 좋음` : '지표가 달라 그래프를 생략합니다'}</span></h2>
        {sameMetric && (
          <ResponsiveContainer width="100%" height={230}>
            <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="i" tickFormatter={(v) => (v === 0 ? '기준' : `#${v}`)} stroke="var(--muted)" fontSize={11} />
              <YAxis tickFormatter={(v) => fmtValue(v, metric)} stroke="var(--muted)" fontSize={11} width={56}
                domain={metric.direction === 'minimize' ? [0, 'auto'] : ['auto', 'auto']} />
              <Tooltip contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8, fontSize: 12 }}
                labelFormatter={(v) => (v === 0 ? '기준선' : `반복 ${v}`)}
                formatter={(v, n) => [fmtValue(v, metric), label(data[Number(n.slice(1))])]} />
              {data.map((r, k) => <Line key={r.id} type="stepAfter" dataKey={`r${k}`} stroke={SLOT[k]} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} connectNulls={false} />)}
            </LineChart>
          </ResponsiveContainer>
        )}
      </section>

      <section className="card compare-table-wrap">
        <table className="compare-table">
          <thead><tr><th />{data.map((r, k) => <th key={r.id}><i style={{ background: SLOT[k] }} />{fmtTime(r.started_at)}</th>)}</tr></thead>
          <tbody>
            {cells.map(([name, fn]) => (
              <tr key={name}><th>{name}</th>{data.map((r) => <td key={r.id}>{fn(r)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
