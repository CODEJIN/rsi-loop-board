import React, { useState } from 'react';
import { api } from '../api.js';
import { fmtValue } from '../labels.js';
import { setSectionField } from '../specText.js';

// 측정만(dry-run) 런의 잡음 분석 — 같은 코드를 여러 번 잰 값의 흩어짐으로 채택 기준을 제안합니다.
// 제안값 = max(3 × 표준편차, 평균의 5%) — Phase 0에서 쓴 규칙과 같습니다. 표본이 적으면 표준편차가 크게 흔들려서
// MIN_SAMPLES 미만이면 기준 반영 버튼을 보여주지 않습니다.
const MIN_SAMPLES = 4;
export default function NoiseReport({ run, iterations }) {
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const values = [run.baseline_objective, ...iterations.filter((i) => i.decision === 'measured').map((i) => i.objective)]
    .filter((v) => typeof v === 'number');
  const metric = run.metric;
  if (values.length < 2) {
    return <div className="card"><h2>잡음 분석</h2><p className="muted small">측정값이 2개 이상 모이면 잡음과 채택 기준을 계산합니다. (현재 {values.length}개)</p></div>;
  }
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const sd = Math.sqrt(values.reduce((a, b) => a + (b - mean) ** 2, 0) / (values.length - 1));
  const suggested = Math.max(3 * sd, Math.abs(mean) * 0.05);
  const rounded = suggested >= 10 ? Math.ceil(suggested / 5) * 5 : Math.round(suggested * 1000) / 1000;

  const apply = async () => {
    setBusy(true); setMsg('');
    try {
      const v = await api(`/api/specs/${encodeURIComponent(run.spec)}/${run.spec_version}`);
      const yaml = setSectionField(v.yaml, 'metric', 'noise_threshold', String(rounded));
      const r = await api('/api/specs', { method: 'POST', body: { yaml, note: `잡음 측정(${run.id})으로 채택 기준 ${rounded} 설정` } });
      setMsg(`v${r.version}을 만들었습니다.`);
      window.location.hash = `#/specs/${encodeURIComponent(r.name)}/v/${r.version}`;
    } catch (e) { setMsg(e.message); }
    setBusy(false);
  };

  return (
    <div className="card">
      <h2>잡음 분석 <span className="muted small">같은 코드를 {values.length}번 측정</span></h2>
      <div className="stats">
        <div className="stat"><div className="stat-label">평균</div><div className="stat-value">{fmtValue(mean, metric)}</div></div>
        <div className="stat"><div className="stat-label">표준편차</div><div className="stat-value">{fmtValue(sd, metric)}</div>
          <div className="stat-sub">{((sd / Math.abs(mean)) * 100).toFixed(1)}%</div></div>
        <div className="stat"><div className="stat-label">범위</div><div className="stat-value">{fmtValue(Math.max(...values) - Math.min(...values), metric)}</div></div>
        <div className="stat"><div className="stat-label">제안 채택 기준</div><div className="stat-value good-text">{fmtValue(rounded, metric)}</div>
          <div className="stat-sub">현재 {run.noise_threshold_ms != null ? fmtValue(run.noise_threshold_ms, metric) : '없음'}</div></div>
      </div>
      {values.length < MIN_SAMPLES && (
        <p className="warn-text small">측정값이 {values.length}개뿐이라 표준편차를 믿기 어렵습니다. 측정만 런을 반복 5회 이상으로 다시 돌린 뒤 기준을 정하세요.</p>
      )}
      {run.status === 'finished' && run.spec && values.length >= MIN_SAMPLES && (
        <button className="primary" style={{ marginTop: 12 }} disabled={busy} onClick={apply}>이 기준으로 Spec 새 버전 만들기</button>
      )}
      {msg && <p className="muted small">{msg}</p>}
    </div>
  );
}
