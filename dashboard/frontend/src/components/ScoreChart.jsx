import React from 'react';
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { DECISION, fmtValue } from '../labels.js';

const TONE_COLOR = { good: 'var(--good)', danger: 'var(--danger)', warn: 'var(--warn)', neutral: 'var(--muted)', info: 'var(--info)' };

// 반복별 측정값(점) + 그 시점까지의 최고 기록(계단선). 기준선(baseline)은 점선.
export default function ScoreChart({ iterations, baseline, metric, onSelect }) {
  let best = baseline;
  const data = iterations.map((it) => {
    if (it.decision === 'accepted' && it.objective != null) best = it.objective;
    return { iteration: it.iteration, objective: it.objective, best, decision: it.decision };
  });
  if (data.length === 0) return <p className="muted">아직 측정된 반복이 없습니다.</p>;
  const fmt = (v) => fmtValue(v, metric);

  const Dot = ({ cx, cy, payload }) => {
    if (payload.objective == null || cx == null) return null;
    const tone = DECISION[payload.decision]?.tone || 'info';
    return <circle cx={cx} cy={cy} r={payload.decision === 'accepted' ? 6 : 4.5} fill={TONE_COLOR[tone]}
      stroke="var(--surface)" strokeWidth={1.5} style={{ cursor: 'pointer' }} onClick={() => onSelect?.(payload.iteration)} />;
  };

  return (
    <div className="chart">
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={[{ iteration: 0, best: baseline }, ...data]} margin={{ top: 10, right: 16, bottom: 0, left: 4 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="iteration" tickFormatter={(v) => (v === 0 ? '기준' : `#${v}`)} stroke="var(--muted)" fontSize={12} />
          <YAxis tickFormatter={fmt} stroke="var(--muted)" fontSize={12} width={60} domain={metric?.direction === 'minimize' ? [0, 'auto'] : ['auto', 'auto']} />
          <Tooltip
            contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 8 }}
            labelFormatter={(v) => (v === 0 ? '기준선' : `반복 ${v}`)}
            formatter={(v, name) => [fmt(v), name === 'best' ? '최고 기록' : '측정값']}
          />
          {baseline != null && <ReferenceLine y={baseline} stroke="var(--muted)" strokeDasharray="4 4" />}
          <Line type="stepAfter" dataKey="best" stroke="var(--good)" strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line dataKey="objective" stroke="transparent" dot={<Dot />} activeDot={false} isAnimationActive={false} connectNulls />
        </LineChart>
      </ResponsiveContainer>
      <div className="legend small muted">
        <span><i className="sw good" />채택</span><span><i className="sw neutral" />개선 부족·측정</span>
        <span><i className="sw danger" />실패</span><span><i className="line good" />최고 기록</span><span><i className="line dashed" />기준선</span>
      </div>
    </div>
  );
}
