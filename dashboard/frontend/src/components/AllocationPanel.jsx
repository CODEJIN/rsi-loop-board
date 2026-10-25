import React, { useState } from 'react';
import { Badge } from './common.jsx';
import { ROLE_META, TIER_LABEL } from './RolesEditor.jsx';
import { fmtTime } from '../labels.js';

const ROLE_SHORT = { planner: '분석', implementer: '구현', reviewer: '검토' };

// 런 상세의 역할·할당 패널 — 시작 설정, 역할별 사용량(추정), 할당 변경 이력, 조기 종료 권고, 최신 가설 후보.
export default function AllocationPanel({ allocation, iterations }) {
  const [allHyp, setAllHyp] = useState(false);
  if (!allocation?.roles && !Object.keys(allocation?.role_wtok || {}).length) return null;
  const usage = allocation.role_usage;
  const wtok = allocation.role_wtok || {};
  const totalW = Object.values(wtok).reduce((a, b) => a + b, 0) || 1;
  const rows = usage
    ? Object.entries(usage.per_role).map(([k, v]) => [k, v.share, `${v.util_pct}%p`])
    : Object.entries(wtok).map(([k, v]) => [k, v / totalW, null]);
  const latestPlan = [...iterations].reverse().find((i) => i.hypotheses);
  const usedTitles = new Set(iterations.map((i) => i.assigned).filter(Boolean));

  return (
    <div className="card">
      <h2>역할 · 할당</h2>
      {allocation.stop_recommended && (
        <div className="approval warn-box"><b>🛑 조기 종료 권고</b> <span className="small">(반복 {allocation.stop_recommended.iteration})</span>
          <p className="small">{allocation.stop_recommended.reason}</p></div>
      )}
      {allocation.roles && (
        <div className="row-gap wrap-gap small">
          {Object.entries(allocation.roles).map(([k, r]) => (
            <Badge key={k} tone={r.enabled === false || r.when === 'off' ? 'neutral' : 'info'}>
              {ROLE_SHORT[k] || k} · {r.enabled === false || r.when === 'off' ? '끔' : `${ROLE_META[k]?.when.find(([v]) => v === r.when)?.[1] || r.when} · ${TIER_LABEL[r.tier] || r.tier}`}
            </Badge>
          ))}
          {allocation.allocation && <Badge tone="neutral">할당 {allocation.allocation.mode === 'auto' ? '자동' : '수동'}{allocation.allocation.run_budget_pct ? ` · 예산 ${allocation.allocation.run_budget_pct}%p` : ''}</Badge>}
        </div>
      )}
      {rows.length > 0 && (
        <div className="block">
          <div className="label">역할별 사용량 {usage ? `(추정, 런 합계 ${usage.total_util_pct}%p — 본인 사용 포함)` : '(진행 중, 가중 토큰 비중)'}</div>
          <div className="bars wide">
            {rows.map(([k, share, util]) => (
              <div className="bar-row" key={k}>
                <span className="bar-label small">{ROLE_SHORT[k] || k}</span>
                <div className="bar"><div className="bar-fill" style={{ width: `${Math.round(share * 100)}%` }} /></div>
                <span className="bar-value small">{Math.round(share * 100)}%{util ? ` · ${util}` : ''}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {latestPlan && (
        <div className="block">
          <div className="label">가설 후보 (반복 {latestPlan.iteration}, {{ start: '런 시작', plateau: '정체', accept: '채택 후', exhausted: '후보 소진' }[latestPlan.hypotheses.reason] || latestPlan.hypotheses.reason})</div>
          {latestPlan.hypotheses.items?.length ? (
            <ol className="hyp-list small">
              {latestPlan.hypotheses.items.slice(0, allHyp ? 5 : 3).map((h) => (
                <li key={h.id} className={usedTitles.has(h.title) ? 'used' : ''}>
                  <details>
                    <summary><span className="clamp-2">{usedTitles.has(h.title) && <span className="good-text">✓ </span>}{h.title}</span></summary>
                    <div className="muted">{h.rationale}</div>
                    <div className="muted">위험 {h.risk}{h.overlaps ? ` · 겹침: ${h.overlaps}` : ''}</div>
                  </details>
                </li>
              ))}
            </ol>
          ) : <p className="muted small">가설을 해석하지 못해 구현 역할 단독으로 진행했습니다.</p>}
          {latestPlan.hypotheses.items?.length > 3 && <button className="ghost small" onClick={() => setAllHyp((v) => !v)}>{allHyp ? '접기' : `+${latestPlan.hypotheses.items.length - 3}개 더`}</button>}
        </div>
      )}
      {allocation.changes.length > 0 && (
        <div className="block">
          <div className="label">할당 변경 이력</div>
          <ul className="commands">
            {allocation.changes.map((c, i) => (
              <li key={i}>
                <span className="mono muted small">{fmtTime(c.ts).slice(6)}</span>
                <Badge tone={c.by === 'auto' ? 'info' : 'neutral'}>{c.by === 'auto' ? '자동' : '사용자'}</Badge>
                <span className="small">반복 {c.iteration} · {(c.changes || []).map((ch) => `${ROLE_SHORT[ch.role] || ch.role} ${Object.entries(ch).filter(([k]) => k !== 'role').map(([k, [a, b]]) => `${k} ${a}→${b}`).join(', ')}`).join(' / ') || '변경 없음'}</span>
                {c.reason && <span className="muted small">— {c.reason}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
