import React from 'react';

// 역할·할당 설정 편집기 (docs/ROLES_DESIGN.md §3) — Spec 편집기, 새 런 시작(덮어쓰기), 실행 중 변경(set_roles)에서 같이 씁니다.
export const ROLE_META = {
  planner: { label: '분석 (planner)', desc: '가설 후보를 순위와 함께 제안', when: [['off', '끔'], ['start_only', '런 시작 시만'], ['start_plateau', '시작 + 정체 시'], ['start_plateau_accept', '시작 + 정체 + 채택 후']] },
  implementer: { label: '구현 (implementer)', desc: '가설 하나를 코드로 구현 (끌 수 없음)', when: [['always', '매 반복']] },
  reviewer: { label: '검토 (reviewer)', desc: '채택 후보의 지표 조작·불변 조건 위반 검사 → 거부 시 사람 승인', when: [['off', '끔'], ['on_accept_candidate', '채택 후보에만']] },
};
export const TIER_LABEL = { light: '경량', standard: '표준', high: '상위' };
export const DEFAULT_ROLES = {
  planner: { enabled: true, when: 'start_plateau_accept', tier: 'standard' },
  implementer: { enabled: true, when: 'always', tier: 'standard' },
  reviewer: { enabled: true, when: 'on_accept_candidate', tier: 'standard' },
};
const DEFAULT_FACTOR = { high: 5, standard: 1, light: 0.25 };
// 예상 비중 미리보기용 대략치 — 실행 빈도(반복당) × 출력 규모. 실제 값은 런이 끝나면 role_usage로 보여줍니다.
const FREQ = { always: 1, start_only: 0.2, start_plateau: 0.35, start_plateau_accept: 0.5, on_accept_candidate: 0.3, off: 0 };
const SIZE = { implementer: 1, planner: 0.6, reviewer: 0.3 };

// roles가 없는 Spec은 구현만 도는 기존 동작 — 편집기에서는 다른 역할을 '꺼짐'으로 보여줍니다.
export const normalizeRoles = (roles) => {
  if (!roles) return { ...structuredClone(DEFAULT_ROLES), planner: { ...DEFAULT_ROLES.planner, enabled: false }, reviewer: { ...DEFAULT_ROLES.reviewer, enabled: false } };
  return Object.fromEntries(Object.keys(DEFAULT_ROLES).map((k) => [k, { ...DEFAULT_ROLES[k], ...(roles[k] || {}) }]));
};

export function estimateShares(roles, factor = DEFAULT_FACTOR) {
  const w = Object.fromEntries(Object.entries(roles).map(([k, r]) => {
    const on = k === 'implementer' || (r.enabled && r.when !== 'off');
    return [k, on ? (FREQ[r.when] ?? 0) * (SIZE[k] ?? 0.5) * (factor[r.tier] ?? 1) : 0];
  }));
  const total = Object.values(w).reduce((a, b) => a + b, 0) || 1;
  return Object.fromEntries(Object.entries(w).map(([k, v]) => [k, v / total]));
}

export default function RolesEditor({ roles, onChange, allocation, onAllocationChange, compact = false, factor }) {
  const r = normalizeRoles(roles);
  const set = (name, patch) => onChange({ ...r, [name]: { ...r[name], ...patch } });
  const shares = estimateShares(r, factor);
  return (
    <div className="roles-editor">
      <table className="roles-table">
        <thead><tr><th>역할</th><th>켜기</th><th>실행 시점</th><th>모델 계층</th><th>예상 비중</th></tr></thead>
        <tbody>
          {Object.entries(ROLE_META).map(([name, meta]) => {
            const cfg = r[name];
            const fixed = name === 'implementer';
            return (
              <tr key={name} className={!fixed && !cfg.enabled ? 'off' : ''}>
                <td><b>{meta.label}</b>{!compact && <div className="muted small">{meta.desc}</div>}</td>
                <td><input type="checkbox" checked={fixed || !!cfg.enabled} disabled={fixed} onChange={(e) => set(name, { enabled: e.target.checked })} /></td>
                <td>
                  <select value={cfg.when} disabled={fixed || !cfg.enabled} onChange={(e) => set(name, { when: e.target.value })}>
                    {meta.when.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </td>
                <td>
                  <select value={cfg.tier} disabled={!fixed && !cfg.enabled} onChange={(e) => set(name, { tier: e.target.value })}>
                    {Object.entries(TIER_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </td>
                <td className="small">{Math.round(shares[name] * 100)}%</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="muted small">예상 비중은 실행 빈도·계층 가중치로 낸 대략치입니다. 실제 비중은 런이 끝나면 런 상세에 표시됩니다.</p>
      {allocation && onAllocationChange && (
        <div className="form-grid">
          <label className="field"><span>할당 모드</span>
            <select value={allocation.mode || 'manual'} onChange={(e) => onAllocationChange({ ...allocation, mode: e.target.value })}>
              <option value="manual">수동 (설정 그대로)</option>
              <option value="auto">자동 (정체·사용량에 따라 조정)</option>
            </select>
          </label>
          <label className="field"><span>런 사용량 예산 (5시간 창 %p, 비우면 제한 없음)</span>
            <input inputMode="decimal" value={allocation.run_budget_pct ?? ''}
              onChange={(e) => onAllocationChange({ ...allocation, run_budget_pct: e.target.value === '' ? null : Number(e.target.value) })} />
          </label>
          <label className="field"><span>정체 판정 (연속 개선 부족 횟수)</span>
            <input inputMode="numeric" value={allocation.plateau_after ?? 2}
              onChange={(e) => onAllocationChange({ ...allocation, plateau_after: Number(e.target.value) || 2 })} />
          </label>
        </div>
      )}
    </div>
  );
}
