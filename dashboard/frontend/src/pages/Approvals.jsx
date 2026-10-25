import React, { useState } from 'react';
import { api } from '../api.js';
import { useApi } from '../hooks.js';
import Icon from '../components/icons.jsx';
import { DiffView } from '../components/IterationDetail.jsx';
import { fmtGain, fmtTime, fmtValue } from '../labels.js';

// 승인함 — 모든 런의 사람 승인 대기를 한곳에서. 모바일에서 알림 → 판단 → 이탈이 한 화면에서 끝나게 합니다.
function ApprovalCard({ a, onDone }) {
  const [busy, setBusy] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [msg, setMsg] = useState('');
  const act = async (type) => {
    if (type === 'reject' && !window.confirm('이 후보를 버립니다. 계속할까요?')) return;
    setBusy(true);
    try { await api('/api/commands', { method: 'POST', body: { type, run_id: a.run_id, payload: { iteration: a.iteration } } }); setMsg(type === 'approve' ? '승인 전달됨' : '거절 전달됨'); onDone(); }
    catch (e) { setMsg(e.message); }
    setBusy(false);
  };
  const reviewTone = a.review?.verdict === 'reject' ? 'danger' : a.review?.verdict === 'pass' ? 'good' : 'neutral';
  return (
    <div className="card approval-card">
      <div className="row-between">
        <a href={`#/runs/${encodeURIComponent(a.run_id)}/iter/${a.iteration}`} className="row-gap"><b>{a.title}</b><span className="muted small">#{a.iteration}</span></a>
        <span className="badge info"><Icon name="user" size={12} /> 승인 필요</span>
      </div>
      <div className="approval-metrics">
        <div><div className="stat-label">측정값</div><div className="stat-value">{fmtValue(a.objective, a.metric)}</div></div>
        <div><div className="stat-label">변화</div><div className="stat-value good-text">{fmtGain(a.improvement, a.metric)}</div></div>
        <div><div className="stat-label">변경</div><div className="stat-value">{a.diff_lines ?? '—'}<span className="small muted">줄</span></div></div>
      </div>
      <p className="small"><Icon name="alert" size={13} /> {a.reason}</p>
      {a.review && (
        <div className={`review-chip t-${reviewTone}`}>
          <Icon name="shield" size={14} /> 검토 {a.review.verdict === 'pass' ? '통과' : a.review.verdict === 'reject' ? '거부' : '해석 실패'} · 위험 {a.review.risk}
        </div>
      )}
      {a.summary && <p className="small muted clamp-2">{a.summary}</p>}
      <div className="approval-actions">
        <button className="ghost" onClick={() => setShowDiff((v) => !v)}><Icon name="code" size={15} /> diff</button>
        <button className="danger" disabled={busy} onClick={() => act('reject')}><Icon name="x" size={15} /> 거절</button>
        <button className="primary" disabled={busy} onClick={() => act('approve')}><Icon name="check" size={15} /> 승인</button>
      </div>
      {msg && <p className="muted small">{msg}</p>}
      {showDiff && <DiffView runId={a.run_id} iteration={a.iteration} />}
    </div>
  );
}

export default function Approvals({ lastEvent }) {
  const { data, error, reload } = useApi('/api/approvals', { refreshKey: lastEvent, pollMs: 15000 });
  if (error) return <p className="error">{error.message}</p>;
  if (!data) return <p className="muted">불러오는 중…</p>;
  return (
    <div className="stack narrow-page">
      <h1 className="run-title">승인함</h1>
      {data.pending.length === 0 ? (
        <div className="card empty-state"><Icon name="check" size={28} /><div>대기 중인 승인이 없습니다</div></div>
      ) : <div className="card-grid-2">{data.pending.map((a) => <ApprovalCard key={`${a.run_id}-${a.iteration}`} a={a} onDone={reload} />)}</div>}
      {data.recent.length > 0 && (
        <section className="card">
          <h2>최근 판정</h2>
          <ul className="history">
            {data.recent.map((r, i) => (
              <li key={i}>
                <span className={`hist-icon ${r.approved ? 'good-text' : 'error'}`}><Icon name={r.approved ? 'check' : 'x'} size={14} /></span>
                <a href={`#/runs/${encodeURIComponent(r.run_id)}/iter/${r.iteration}`} className="ellipsis">{r.title} #{r.iteration}</a>
                <span className="muted small">{fmtTime(r.ts)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
