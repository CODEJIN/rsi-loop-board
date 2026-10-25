import React, { useEffect, useState } from 'react';
import { api } from '../api.js';
import { DecisionBadge } from './common.jsx';
import Icon from './icons.jsx';
import { CurveChart, Stepper } from './viz.jsx';
import { PHASE, fmtDuration, fmtGain, fmtTime, fmtValue, gateLabel } from '../labels.js';

export function DiffView({ runId, iteration }) {
  const [state, setState] = useState({ loading: true });
  useEffect(() => {
    setState({ loading: true });
    api(`/api/runs/${encodeURIComponent(runId)}/iterations/${iteration}/diff`)
      .then((d) => setState({ data: d })).catch((e) => setState({ error: e.message }));
  }, [runId, iteration]);
  if (state.loading) return <p className="muted small">diff 불러오는 중…</p>;
  if (state.error) return <p className="muted small">{state.error}</p>;
  return (
    <pre className="diff">
      {state.data.diff.split('\n').map((line, i) => {
        const cls = line.startsWith('+') && !line.startsWith('+++') ? 'add'
          : line.startsWith('-') && !line.startsWith('---') ? 'del' : line.startsWith('@@') ? 'hunk' : '';
        return <span key={i} className={cls}>{line}{'\n'}</span>;
      })}
      {state.data.truncated && <span className="muted">… (잘림)</span>}
    </pre>
  );
}

// 평가 단계 파이프라인 — 단계마다 통과·탈락·조기 종료와 값(같은 단계 best 대비), 그리고 학습 곡선.
function EvalStages({ it, metric }) {
  const done = it.eval_stages || [];
  const of = done[0]?.of || (it.live?.length ? done.length + 1 : done.length);
  const live = it.live?.[0];
  const withCurve = [...done].reverse().find((s) => s.curve?.length > 1);
  const chart = live ? { curve: live.curve, best: live.best_curve, stopped: false, stage: live.stage }
    : withCurve ? { curve: withCurve.curve, best: withCurve.best_curve, stopped: withCurve.early_stopped, stage: withCurve.stage } : null;
  return (
    <div className="block">
      <div className="label">평가 단계</div>
      <ol className="eval-stages">
        {Array.from({ length: of }, (_, i) => {
          const s = done.find((x) => x.index === i);
          const running = !s && live && i === done.length;
          const st = s ? (s.passed ? 'pass' : s.early_stopped ? 'early' : 'fail') : running ? 'running' : 'skip';
          return (
            <li key={i} className={`es es-${st}`}>
              <span className="es-icon"><Icon name={st === 'pass' ? 'check' : st === 'fail' ? 'x' : st === 'early' ? 'skip' : st === 'running' ? 'clock' : 'circle'} size={13} /></span>
              <span className="es-main">
                <b className="small">{s?.stage || (running ? live.stage : done[0]?.names?.[i] || `단계 ${i + 1}`)}</b>
                <span className="muted small">{s ? (s.objective != null ? `${fmtValue(s.objective, metric)}${s.best_objective != null ? ` · best ${fmtValue(s.best_objective, metric)}` : ''}` : s.early_stopped ? '조기 종료' : '실패')
                  + ` · ${fmtDuration((s.seconds || 0) * 1000)}` : running ? `진행 중 · step ${live.curve.at(-1)?.step ?? '—'}${live.curve.at(-1)?.total ? `/${live.curve.at(-1).total}` : ''}` : '실행 안 함'}</span>
              </span>
            </li>
          );
        })}
      </ol>
      {chart && <><div className="muted small">{chart.stage} 단계 학습 곡선</div><CurveChart curve={chart.curve} best={chart.best} stopped={chart.stopped} /></>}
    </div>
  );
}

export default function IterationDetail({ runId, it, metric, onClose }) {
  const [showDiff, setShowDiff] = useState(false);
  useEffect(() => setShowDiff(false), [it.iteration]);
  const groups = it.breakdown ? Object.entries(it.breakdown).filter(([, v]) => typeof v === 'number') : [];
  const maxGroup = Math.max(1, ...groups.map(([, v]) => v));

  return (
    <div className="card iteration-detail">
      <div className="row-between">
        <h2>반복 {it.iteration} <DecisionBadge decision={it.decision} /></h2>
        <button className="ghost small" onClick={onClose} aria-label="닫기">✕</button>
      </div>
      <div className="muted small">{fmtTime(it.started_at)} → {fmtTime(it.ended_at)} · {PHASE[it.phase]}</div>
      <Stepper it={it} />

      {it.stages?.length > 0 && (
        <div className="block">
          <details><summary className="label">단계 상세 (모델·시간·토큰)</summary>
          <ol className="stages small">
            {it.stages.map((s, i) => (
              <li key={i} className={s.ok === false ? 'fail' : s.ok ? 'pass' : 'running'}>
                <b>{{ planner: '분석', implementer: '구현', reviewer: '검토' }[s.role] || s.role}</b>
                <span className="muted"> · {s.model} ({s.tier})</span>
                {s.ok === undefined ? <span className="muted"> · 진행 중…</span>
                  : <span className="muted"> · {fmtDuration(s.duration_ms)} · 가중 토큰 {s.wtok?.toLocaleString()}{s.util_after != null && s.util_before != null ? ` · 창 ${Math.round(s.util_before * 100)}→${Math.round(s.util_after * 100)}%` : ''}</span>}
                {s.error && <div className="error">{s.error}</div>}
              </li>
            ))}
          </ol></details>
          {it.warnings?.map((w, i) => <p key={i} className="warn-text small">⚠ {w}</p>)}
        </div>
      )}

      {it.assigned && <div className="block"><div className="label">배정된 가설 (분석 역할)</div><p className="small">{it.assigned}</p></div>}

      {it.summary && (
        <div className="block">
          <div className="label">에이전트 변경 요약</div>
          <p className="clamp-3" title={it.summary}>{it.summary}</p>
          {it.hypothesis && <details className="small"><summary>가설 보기</summary><p className="muted">{it.hypothesis}</p></details>}
          <div className="muted small">도구 호출 {it.tool_calls}회 · {it.num_turns ?? '—'}턴 · {fmtDuration(it.agent_duration_ms)} · 출력 {it.output_tokens?.toLocaleString() ?? '—'} 토큰</div>
        </div>
      )}

      <div className="block">
        <div className="label">검사</div>
        {it.gates.length === 0 ? <p className="muted small">아직 없음</p> : (
          <>
            <div className="gate-chips">
              {it.gates.map((g, i) => (
                <span key={i} className={`gate-chip ${g.passed ? 'pass' : 'fail'}`}><Icon name={g.passed ? 'check' : 'x'} size={12} />{gateLabel(g.gate)}</span>
              ))}
            </div>
            {it.gates.filter((g) => !g.passed && g.detail).map((g, i) => (
              <pre key={i} className="gate-detail">{typeof g.detail === 'string' ? g.detail : JSON.stringify(g.detail, null, 1)}</pre>
            ))}
          </>
        )}
      </div>

      {(it.eval_stages?.length > 0 || it.live?.length > 0) && <EvalStages it={it} metric={metric} />}

      {it.objective != null && (
        <div className="block">
          <div className="label">측정 결과 — {metric.name} {fmtValue(it.objective, metric)}
            {it.improvement != null && <span className={it.improvement > 0 ? 'good-text' : 'muted'}> ({fmtGain(it.improvement, metric)}, 직전 최고 대비)</span>}
          </div>
          <div className="bars">
            {groups.map(([k, v]) => (
              <div className="bar-row" key={k}>
                <span className="bar-label mono small">{k}</span>
                <div className="bar"><div className="bar-fill" style={{ width: `${(v / maxGroup) * 100}%` }} /></div>
                <span className="bar-value small">{fmtValue(v, metric)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {it.review && (
        <div className="block">
          <div className="label">검토 (채택 후보 검사)</div>
          <p className="small"><b className={it.review.verdict === 'pass' ? 'good-text' : 'error'}>{it.review.verdict === 'pass' ? '통과' : it.review.verdict === 'reject' ? '거부' : '해석 실패'}</b> · 위험 {it.review.risk}</p>
          {it.review.reasons?.[0] && <p className="small muted clamp-2">{it.review.reasons[0].replace(/\*\*/g, '')}</p>}
          {it.review.reasons?.length > 1 && (
            <details className="small"><summary>근거 전체 ({it.review.reasons.length})</summary>
              <ul>{it.review.reasons.map((r, i) => <li key={i}>{r.replace(/\*\*/g, '')}</li>)}</ul></details>
          )}
        </div>
      )}

      {it.approval && (
        <div className="block">
          <div className="label">사람 승인</div>
          <p className="small">{it.approval.reason}</p>
          <p className="small">{it.approval_pending ? '승인 대기 중' : it.approved ? '✓ 승인됨' : '✗ 거절됨'}</p>
        </div>
      )}

      {it.commit && (
        <div className="block">
          <div className="label">변경 파일 <span className="muted small mono">{it.commit} · {it.diff_stat}</span></div>
          <ul className="files mono small">{it.files_touched.map((f) => <li key={f}>{f}</li>)}</ul>
          <button className="ghost small" onClick={() => setShowDiff((v) => !v)}>{showDiff ? 'diff 숨기기' : 'diff 보기'}</button>
          {showDiff && <DiffView runId={runId} iteration={it.iteration} />}
        </div>
      )}
    </div>
  );
}
