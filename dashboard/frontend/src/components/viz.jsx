import React from 'react';
import Icon from './icons.jsx';
import { DECISION, fmtValue } from '../labels.js';

// ---- 상태 색 (status: 아이콘+라벨과 함께만 씀 — 색만으로 의미를 전달하지 않음)
const STATUS_OF = (v) => (v == null ? 'neutral' : v >= 0.8 ? 'critical' : v >= 0.6 ? 'warning' : 'good');
const STATUS_LABEL = { good: '여유', warning: '주의', critical: '한도 근접', neutral: '기록 없음' };
const STATUS_ICON = { good: 'check', warning: 'alert', critical: 'alert', neutral: 'clock' };

const resetText = (epoch) => {
  if (!epoch) return null;
  const ms = epoch * 1000 - Date.now();
  if (ms <= 0) return '초기화됨';
  const h = Math.floor(ms / 3600000), m = Math.round((ms % 3600000) / 60000);
  return h >= 24 ? `${Math.round(h / 24)}일 후 초기화` : h ? `${h}시간 ${m}분 후 초기화` : `${m}분 후 초기화`;
};

// 사용량 링 게이지 — 숫자 하나가 핵심인 정보라 차트 대신 링 + 큰 숫자(hero)로 보여줍니다.
export function UsageRing({ label, value, reset, size = 104 }) {
  // 초기화 시각이 지났다면 표시값은 지난 창의 기록 — 현재 값처럼 경고 색으로 보이지 않게 회색 처리합니다.
  const stale = reset && reset * 1000 < Date.now();
  const st = stale ? 'neutral' : STATUS_OF(value);
  const r = 42, c = 2 * Math.PI * r;
  const pct = value == null ? 0 : Math.min(1, value);
  return (
    <div className="ring" title={`${label} 사용률 ${value == null ? '—' : Math.round(value * 100) + '%'}`}>
      <svg viewBox="0 0 100 100" width={size} height={size} role="img" aria-label={`${label} ${value == null ? '기록 없음' : Math.round(value * 100) + '%'}`}>
        <circle cx="50" cy="50" r={r} className="ring-track" />
        <circle cx="50" cy="50" r={r} className={`ring-fill st-${st}`} strokeDasharray={`${pct * c} ${c}`} transform="rotate(-90 50 50)" />
        <text x="50" y="48" className="ring-value">{value == null ? '—' : `${Math.round(value * 100)}`}<tspan className="ring-unit">{value == null ? '' : '%'}</tspan></text>
        <text x="50" y="66" className="ring-label">{label}</text>
      </svg>
      <div className={`ring-status st-text-${st}`}><Icon name={STATUS_ICON[st]} size={13} /> {stale ? '이전 기록' : STATUS_LABEL[st]}</div>
      {reset && <div className="ring-reset muted">{resetText(reset)}</div>}
    </div>
  );
}

// 스파크라인 — 런의 반복별 측정값. 채택된 점은 강조, 나머지는 작게. 마우스를 올리면 값(title)이 보입니다.
export function Sparkline({ series, baseline, metric, width = 120, height = 34 }) {
  const pts = series.filter((p) => p.v != null);
  const vals = [baseline, ...pts.map((p) => p.v)].filter((v) => v != null);
  if (vals.length < 2) return <span className="spark-empty muted small">—</span>;
  const min = Math.min(...vals), max = Math.max(...vals), span = max - min || 1;
  const n = Math.max(series.length, 1);
  const x = (i) => 4 + (i / n) * (width - 8);
  const y = (v) => height - 4 - ((v - min) / span) * (height - 8);
  let best = baseline;
  const bestPath = [`M${x(0)},${y(baseline ?? pts[0].v)}`];
  series.forEach((p, idx) => {
    if (p.d === 'accepted' && p.v != null) best = p.v;
    if (best != null) bestPath.push(`L${x(idx + 1)},${y(best)}`);
  });
  return (
    <svg className="spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="반복별 측정값 추이">
      <path d={bestPath.join(' ')} className="spark-best" />
      {series.map((p, idx) => p.v != null && (
        <circle key={idx} cx={x(idx + 1)} cy={y(p.v)} r={p.d === 'accepted' ? 3.5 : 2.2}
          className={`spark-dot ${p.d === 'accepted' ? 'acc' : ''}`}>
          <title>{`반복 ${p.i}: ${fmtValue(p.v, metric)} · ${DECISION[p.d]?.label || p.d || '진행 중'}`}</title>
        </circle>
      ))}
    </svg>
  );
}

// 판정 점 — 반복마다 하나. 색 + 아이콘 모양으로 구분(채택 ✓ / 실패 ✗ / 개선 부족 · / 진행 중 ○).
const DOT_ICON = { good: 'check', danger: 'x', warn: 'alert', neutral: null, info: null };
export function DecisionDots({ series, onSelect, selected }) {
  return (
    <div className="dots">
      {series.map((p) => {
        const tone = p.d ? (DECISION[p.d]?.tone || 'neutral') : 'running';
        const icon = DOT_ICON[tone];
        return (
          <button key={p.i} type="button" className={`dot t-${tone} ${selected === p.i ? 'sel' : ''}`} onClick={onSelect ? () => onSelect(p.i) : undefined}
            title={`반복 ${p.i}: ${p.d ? DECISION[p.d]?.label || p.d : '진행 중'}`} aria-label={`반복 ${p.i}`}>
            {icon ? <Icon name={icon} size={11} /> : <span className="dot-num">{p.i}</span>}
          </button>
        );
      })}
    </div>
  );
}

// 반복 진행 단계 — 분석 → 구현 → 평가 → 검토 → 판정. 각 단계는 아이콘 + 상태(완료·진행·실패·건너뜀).
const STEPS = [
  ['planner', 'bulb', '분석'], ['implementer', 'code', '구현'], ['eval', 'beaker', '평가'], ['reviewer', 'shield', '검토'], ['judge', 'gavel', '판정'],
];
export function stepStates(it) {
  if (!it) return {};
  const st = {};
  const stage = (role) => (it.stages || []).filter((s) => s.role === role).at(-1);
  const p = stage('planner');
  st.planner = p ? (p.ok === undefined ? 'active' : p.ok ? 'done' : 'fail') : 'skip';
  const im = stage('implementer');
  st.implementer = it.agent_done ? (it.agent_timed_out ? 'fail' : 'done') : im ? 'active' : (it.phase === 'agent' ? 'active' : 'wait');
  const failedGate = it.gates?.find((g) => !g.passed);
  st.eval = it.objective != null ? 'done' : failedGate ? 'fail' : it.phase === 'benchmarking' || it.phase === 'building' ? 'active' : 'wait';
  const rv = stage('reviewer');
  st.reviewer = rv ? (rv.ok === undefined ? 'active' : it.review?.verdict === 'reject' ? 'fail' : 'done') : 'skip';
  st.judge = it.decision ? (it.decision === 'accepted' ? 'done' : 'fail') : it.phase === 'approval' ? 'active' : 'wait';
  if (it.phase === 'interrupted') for (const k of Object.keys(st)) if (st[k] === 'active' || st[k] === 'wait') st[k] = 'skip';
  return st;
}
export function Stepper({ it, compact = false }) {
  const st = stepStates(it);
  return (
    <ol className={`stepper ${compact ? 'compact' : ''}`}>
      {STEPS.map(([key, icon, label]) => (
        <li key={key} className={`step s-${st[key] || 'wait'}`} title={`${label}: ${{ done: '완료', active: '진행 중', fail: '실패/거부', skip: '건너뜀', wait: '대기' }[st[key] || 'wait']}`}>
          <span className="step-icon"><Icon name={st[key] === 'fail' ? 'x' : icon} size={compact ? 13 : 15} /></span>
          {!compact && <span className="step-label">{label}</span>}
        </li>
      ))}
    </ol>
  );
}

// 가로 막대 한 줄 — 비중(%)을 길이로, 값은 항상 텍스트로 옆에 표시(색만으로 구분하지 않음).
export function ShareBar({ items }) {
  const total = items.reduce((a, b) => a + (b.value || 0), 0) || 1;
  return (
    <div className="sharebar">
      <div className="sharebar-track" role="img" aria-label={items.map((i) => `${i.label} ${Math.round((i.value / total) * 100)}%`).join(', ')}>
        {items.map((i) => (
          <span key={i.label} className={`seg ${i.cls || ''}`} style={{ flexGrow: i.value || 0 }} title={`${i.label} ${Math.round((i.value / total) * 100)}%`} />
        ))}
      </div>
      <div className="sharebar-legend">
        {items.map((i) => (
          <span key={i.label} className="small"><i className={`sw ${i.cls || ''}`} />{i.label} <b>{Math.round((i.value / total) * 100)}%</b>{i.sub ? <span className="muted"> {i.sub}</span> : null}</span>
        ))}
      </div>
    </div>
  );
}

// 학습 곡선 — 후보(실선)와 같은 단계의 best(점선)를 같은 축에. 조기 종료면 마지막 지점에 ✗ 표시.
export function CurveChart({ curve, best = [], stopped = false, width = 320, height = 120 }) {
  const pts = (curve || []).filter((p) => p.value != null);
  const ref = (best || []).filter((p) => p.value != null);
  if (pts.length + ref.length < 2) return <p className="muted small">곡선 기록이 아직 없습니다.</p>;
  const all = [...pts, ...ref];
  const total = Math.max(...all.map((p) => p.total || p.step));
  const vmin = Math.min(...all.map((p) => p.value)), vmax = Math.max(...all.map((p) => p.value)), span = vmax - vmin || 1;
  const pad = { l: 34, r: 8, t: 8, b: 18 };
  const x = (s) => pad.l + (s / (total || 1)) * (width - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - vmin) / span) * (height - pad.t - pad.b);
  const path = (ps) => ps.map((p, i) => `${i ? 'L' : 'M'}${x(p.step).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
  const last = pts.at(-1);
  return (
    <figure className="curve">
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label="학습 곡선: 후보와 best 비교">
        {[vmin, (vmin + vmax) / 2, vmax].map((v, i) => (
          <g key={i}><line x1={pad.l} x2={width - pad.r} y1={y(v)} y2={y(v)} className="curve-grid" />
            <text x={pad.l - 4} y={y(v) + 3} className="curve-tick" textAnchor="end">{Number(v.toPrecision(3))}</text></g>
        ))}
        <text x={width - pad.r} y={height - 4} className="curve-tick" textAnchor="end">step {Number(total.toPrecision(4))}</text>
        {ref.length > 1 && <path d={path(ref)} className="curve-best" />}
        {pts.length > 1 && <path d={path(pts)} className="curve-cand" />}
        {last && (stopped
          ? <g className="curve-stop"><line x1={x(last.step) - 4} y1={y(last.value) - 4} x2={x(last.step) + 4} y2={y(last.value) + 4} /><line x1={x(last.step) - 4} y1={y(last.value) + 4} x2={x(last.step) + 4} y2={y(last.value) - 4} /></g>
          : <circle cx={x(last.step)} cy={y(last.value)} r="3" className="curve-dot" />)}
      </svg>
      <figcaption className="legend small">
        <span><i className="line s1" /> 이번 후보</span>{ref.length > 1 && <span><i className="line dashed" /> best</span>}
        {stopped && <span className="warn-text">✗ 조기 종료 지점</span>}
      </figcaption>
    </figure>
  );
}
