import React from 'react';
import { DECISION, RUN_STATUS, fmtPct, fmtTime } from '../labels.js';

export const Badge = ({ tone = 'neutral', children }) => <span className={`badge ${tone}`}>{children}</span>;

export const StatusBadge = ({ status }) => {
  const s = RUN_STATUS[status] || { label: status || '—', tone: 'neutral' };
  return <Badge tone={s.tone}>{status === 'running' && <span className="pulse" />}{s.label}</Badge>;
};

export const DecisionBadge = ({ decision }) => {
  if (!decision) return null;
  const d = DECISION[decision] || { label: decision, tone: 'neutral' };
  return <Badge tone={d.tone}>{d.label}</Badge>;
};

// 구독 사용량 게이지 — 값은 Claude Code가 내보내는 rate_limit_event의 utilization(0~1).
export function UsageGauges({ usage, compact = false }) {
  const w = usage?.windows || {};
  const items = [
    { key: 'five_hour', label: '5시간' },
    { key: 'seven_day', label: '7일' },
  ];
  return (
    <div className={`usage ${compact ? 'compact' : ''}`}>
      {items.map(({ key, label }) => {
        const v = w[key];
        const tone = v == null ? 'neutral' : v >= 0.8 ? 'danger' : v >= 0.6 ? 'warn' : 'good';
        return (
          <div className="usage-row" key={key}>
            <div className="usage-head"><span>{label}</span><b>{fmtPct(v)}</b></div>
            <div className="meter"><div className={`meter-fill ${tone}`} style={{ width: `${Math.min(100, (v || 0) * 100)}%` }} /></div>
          </div>
        );
      })}
      <div className="muted small">{usage ? `마지막 갱신 ${fmtTime(usage.ts)} (루프 실행 중에만 갱신)` : '아직 사용량 기록 없음'}</div>
    </div>
  );
}

export const Stat = ({ label, value, sub, tone }) => (
  <div className="stat">
    <div className="stat-label">{label}</div>
    <div className={`stat-value ${tone || ''}`}>{value}</div>
    {sub && <div className="stat-sub">{sub}</div>}
  </div>
);
