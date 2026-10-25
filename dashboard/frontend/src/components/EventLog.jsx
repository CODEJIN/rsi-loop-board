import React, { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import { EVENT_LABEL, fmtGain, fmtTime, fmtValue } from '../labels.js';

const describe = (e, metric) => {
  switch (e.type) {
    case 'agent_tool': return `${e.tool} ${(e.target || '').split('/').pop()}`;
    case 'agent_done': return e.summary;
    case 'gate': return `${e.gate}: ${e.passed ? '통과' : '실패'}`;
    case 'metric': return `${metric?.name || '지표'} ${fmtValue(e.objective ?? e.objective_total_wait_ms, metric)}`;
    case 'judgment': return `${e.decision} ${fmtGain(e.improvement ?? e.improvement_ms, metric)}`;
    case 'approval_required': return `${e.reason} · ${fmtGain(e.improvement, metric)}`;
    case 'approval': return e.approved ? '승인' : '거절';
    case 'role_start': return `${e.role} · ${e.tier || ''} · ${e.model}`;
    case 'role_done': return `${e.role} ${e.ok ? '완료' : '실패'} · ${Math.round((e.duration_ms || 0) / 1000)}초 · 가중 토큰 ${e.wtok}${e.error ? ` · ${e.error}` : ''}`;
    case 'hypotheses': return `${e.items?.length || 0}개 (${e.reason})${e.ok ? '' : ' — 해석 실패'}`;
    case 'hypothesis_assigned': return e.title;
    case 'review': return `${e.verdict} · 위험 ${e.risk} · ${(e.reasons || []).slice(0, 2).join(' / ')}`;
    case 'roles_changed': return `${e.by === 'auto' ? '자동' : '사용자'} · ${e.reason || ''}`;
    case 'role_usage': return Object.entries(e.per_role || {}).map(([k, v]) => `${k} ${Math.round(v.share * 100)}%`).join(' · ');
    case 'stop_recommended': return e.reason;
    case 'model_fallback': return `${e.role}: ${e.model} → 기본 모델`;
    case 'usage': return Object.entries(e.windows || {}).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(' · ');
    case 'candidate_commit': return `${e.commit} ${e.diff_stat || ''}`;
    case 'paused': return e.reason + (e.resume_at ? ` (재개 예정 ${e.resume_at.slice(11, 16)})` : '');
    case 'resumed': return e.reason || '';
    case 'instruction_applied': return (e.texts || []).join(' / ');
    case 'run_aborted': case 'run_end': return e.reason || '';
    case 'baseline_measured': return `${e.source === 'cache' ? '저장된 값' : '새로 측정'} ${fmtValue(e.objective ?? e.objective_total_wait_ms, metric)} (${e.best_ref})`;
    default: return '';
  }
};

// 런의 이벤트 원본 로그 — 처음에 전체를 받고, 이후엔 실시간 이벤트를 뒤에 붙입니다.
export default function EventLog({ runId, lastEvent, metric }) {
  const [events, setEvents] = useState([]);
  const [hideTools, setHideTools] = useState(true);
  const boxRef = useRef(null);

  useEffect(() => {
    setEvents([]);
    api(`/api/runs/${encodeURIComponent(runId)}/events?limit=2000`).then(setEvents).catch(() => {});
  }, [runId]);

  useEffect(() => {
    if (!lastEvent || lastEvent.run !== runId || lastEvent.seq == null) return;
    setEvents((prev) => (prev.some((e) => e.seq === lastEvent.seq) ? prev : [...prev, lastEvent]));
  }, [lastEvent, runId]);

  useEffect(() => {
    const el = boxRef.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 80) el.scrollTop = el.scrollHeight;
  }, [events]);

  const shown = hideTools ? events.filter((e) => e.type !== 'agent_tool') : events;
  return (
    <div>
      <div className="row-between">
        <span />
        <label className="check small"><input type="checkbox" checked={hideTools} onChange={(e) => setHideTools(e.target.checked)} />도구 호출 숨기기</label>
      </div>
      <div className="log" ref={boxRef}>
        {shown.map((e) => (
          <div key={e.seq} className={`log-line t-${e.type}`}>
            <span className="mono muted">{fmtTime(e.ts).slice(6)}</span>
            <span className="log-type">{EVENT_LABEL[e.type] || e.type}{e.iteration ? ` #${e.iteration}` : ''}</span>
            <span className="log-text">{describe(e, metric)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
