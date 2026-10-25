import React, { useState } from 'react';
import { api } from '../api.js';
import { useApi } from '../hooks.js';
import { Badge } from './common.jsx';
import RolesEditor, { normalizeRoles } from './RolesEditor.jsx';
import { COMMAND, COMMAND_STATUS, fmtGain, fmtTime } from '../labels.js';

const send = (type, run_id, payload = {}) => api('/api/commands', { method: 'POST', body: { type, run_id, payload } });

export function CommandHistory({ runId, lastEvent }) {
  const { data } = useApi(`/api/commands?run_id=${encodeURIComponent(runId)}`, {
    refreshKey: lastEvent, shouldRefresh: (ev) => !ev || ev.run === runId || ev.type === 'command', debounceMs: 300,
  });
  if (!data || data.length === 0) return null;
  return (
    <ul className="commands">
      {data.map((c) => {
        const st = COMMAND_STATUS[c.status] || { label: c.status, tone: 'neutral' };
        return (
          <li key={c.id}>
            <span className="mono muted small">{fmtTime(c.created_at).slice(6)}</span>
            <span>{COMMAND[c.type]?.label || c.type}{c.type === 'inject' && <span className="muted"> — “{c.payload.text}”</span>}</span>
            <Badge tone={st.tone}>{st.label}</Badge>
            {c.status === 'delivered' && <span className="muted small">반영 시점: {COMMAND[c.type]?.when}</span>}
            {c.result && ['rejected', 'failed', 'expired'].includes(c.status) && <span className={c.status === 'expired' ? 'muted small' : 'error small'}>{c.result}</span>}
          </li>
        );
      })}
    </ul>
  );
}

// 런 상세의 제어 패널 — 실행 중/일시정지 상태에서만 보입니다.
export function RunControls({ run, orchestrator, lastEvent, onShowIteration, currentRoles }) {
  const [roleDraft, setRoleDraft] = useState(null);   // null = 편집 안 함
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const active = run.status === 'running' || run.status === 'paused';

  const act = async (type, payload, confirmText) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(true); setError('');
    try {
      await send(type, run.id, payload);
      if (type === 'inject') setText('');
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  return (
    <div className="card">
      <div className="row-between">
        <h2>제어</h2>
        {!orchestrator?.online && <Badge tone="danger">오케스트레이터 연결 안 됨 — 명령이 대기열에 쌓입니다</Badge>}
      </div>
      {run.pending_approval && (
        <div className="approval">
          <div className="row-between wrap">
            <b>🙋 반복 {run.pending_approval.iteration} 승인 필요</b>
            <span className="small">{fmtGain(run.pending_approval.improvement, run.metric)} · 변경 {run.pending_approval.diff_lines}줄</span>
          </div>
          <p className="small muted">{run.pending_approval.reason}</p>
          <div className="control-buttons">
            <button className="ghost" onClick={() => onShowIteration?.(run.pending_approval.iteration)}>변경 내용 보기</button>
            <button className="primary" disabled={busy} onClick={() => act('approve', { iteration: run.pending_approval.iteration })}>✓ 승인 (best에 반영)</button>
            <button className="danger" disabled={busy} onClick={() => act('reject', { iteration: run.pending_approval.iteration }, '이 후보를 버립니다. 계속할까요?')}>✗ 거절</button>
          </div>
        </div>
      )}
      {active ? (
        <>
          <div className="control-buttons">
            {run.status === 'paused'
              ? <button className="primary" disabled={busy} onClick={() => act('resume')}>▶ 재개</button>
              : <button disabled={busy} onClick={() => act('pause')}>⏸ 일시정지</button>}
            <button disabled={busy} onClick={() => act('stop_after', {}, '현재 반복이 끝나면 런을 종료합니다. 계속할까요?')}>⏭ 이번 반복 후 종료</button>
            <button className="danger" disabled={busy} onClick={() => act('stop', {}, '진행 중인 반복을 버리고 즉시 중단합니다. 계속할까요?')}>⏹ 즉시 중단</button>
          </div>
          <div className="inject">
            <textarea rows={2} maxLength={2000} placeholder="다음 반복에 에이전트에게 전달할 추가 지시 (예: useMemo 대신 컴포넌트 분리 방향으로 시도해봐)"
              value={text} onChange={(e) => setText(e.target.value)} />
            <button disabled={busy || !text.trim()} onClick={() => act('inject', { text })}>➕ 지시 보내기</button>
          </div>
          <p className="muted small">일시정지는 다음 체크포인트에서, 추가 지시는 다음 반복 시작 시 반영됩니다. 즉시 중단은 바로 적용됩니다.</p>
        </>
      ) : <p className="muted small">실행 중인 런이 아니라 제어할 수 없습니다.</p>}
      {error && <p className="error small">{error}</p>}
      {active && !run.dry_run && (
        <details className="block" onToggle={(e) => setRoleDraft(e.currentTarget.open ? normalizeRoles(currentRoles) : null)}>
          <summary className="small">역할 · 계층 바꾸기 (다음 반복부터 반영)</summary>
          {roleDraft && (
            <>
              <RolesEditor roles={roleDraft} onChange={setRoleDraft} compact />
              <button disabled={busy} onClick={() => act('set_roles', { roles: roleDraft })}>적용</button>
            </>
          )}
        </details>
      )}
      <CommandHistory runId={run.id} lastEvent={lastEvent} />
    </div>
  );
}

// 새 런 시작 폼 — Overview와 Spec 화면에서 씁니다. fixedSpec을 주면 그 Spec·버전으로 고정됩니다.
export function StartRun({ orchestrator, usage, fixedSpec = null }) {
  const { data: specList } = useApi(fixedSpec ? null : '/api/specs');
  const specs = fixedSpec ? [fixedSpec] : (specList || []);
  const [spec, setSpec] = useState('');
  const [iterations, setIterations] = useState('');
  const [dryRun, setDryRun] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const chosen = specs.find((s) => s.name === spec) || specs[0];
  const busyRun = orchestrator?.state?.active_run;
  const five = usage?.windows?.five_hour;
  const needsNoise = chosen && chosen.metric?.noise_threshold == null;
  // 이 런에만 적용할 역할·할당 덮어쓰기 — 펼치면 선택한 Spec 버전의 설정을 불러와 시작점으로 씁니다.
  const [override, setOverride] = useState(null);
  const loadOverride = async (open) => {
    if (!open || !chosen) { setOverride(null); return; }
    try {
      const v = await api(`/api/specs/${encodeURIComponent(chosen.name)}/${chosen.version}`);
      setOverride({ roles: normalizeRoles(v.data.roles), allocation: { mode: 'manual', run_budget_pct: null, plateau_after: 2, ...(v.data.allocation || {}) } });
    } catch { setOverride(null); }
  };

  const start = async () => {
    const dry = dryRun || needsNoise;
    const n = Number(iterations || (dry ? 5 : chosen?.max_iterations) || 5);
    const note = dry ? '에이전트 없이 현재 최고 버전을 측정만 합니다(사용량 없음).' : '구독 사용량이 소모됩니다.';
    if (!window.confirm(`${chosen.name} v${chosen.version} 런을 반복 ${n}회로 시작합니다. ${note} 계속할까요?`)) return;
    setBusy(true); setMsg('');
    try {
      await send('start_run', null, { spec: chosen.name, version: chosen.version, iterations: n, dry_run: dry,
        ...(override && !dry ? { roles: override.roles, allocation: override.allocation } : {}) });
      setMsg('시작 명령을 보냈습니다. 잠시 후 실행 중 목록에 나타납니다.');
    } catch (e) { setMsg(e.message); }
    setBusy(false);
  };

  if (!orchestrator?.online) return <p className="muted small">오케스트레이터가 연결되지 않아 새 런을 시작할 수 없습니다.</p>;
  if (specs.length === 0) return <p className="muted small">Spec이 없습니다. <a href="#/specs/new">새 Spec 만들기</a></p>;
  return (
    <div className="start-run">
      <div className="start-fields">
        {!fixedSpec && (
          <label className="field">
            <span>Spec</span>
            <select value={chosen?.name} onChange={(e) => setSpec(e.target.value)}>
              {specs.map((s) => <option key={s.name} value={s.name}>{s.title || s.name} (v{s.version})</option>)}
            </select>
          </label>
        )}
        <label className="field narrow">
          <span>반복</span>
          <input inputMode="numeric" placeholder={String(dryRun || needsNoise ? 5 : chosen?.max_iterations || 5)} value={iterations}
            onChange={(e) => setIterations(e.target.value.replace(/\D/g, ''))} />
        </label>
      </div>
      {!fixedSpec && chosen?.goal && <p className="muted small">{chosen.goal}</p>}
      <label className="check small"><input type="checkbox" checked={dryRun || needsNoise} disabled={needsNoise}
        onChange={(e) => setDryRun(e.target.checked)} />측정만 (dry-run — 에이전트 없이 현재 최고 버전을 반복 측정해 잡음 확인, 사용량 없음)</label>
      {needsNoise && <p className="warn-text small">이 버전은 채택 기준(noise_threshold)이 없어 측정만 가능합니다. 측정 후 잡음 분석에서 기준을 정하세요.</p>}
      {!dryRun && !needsNoise && (
        <details className="block" onToggle={(e) => loadOverride(e.currentTarget.open)}>
          <summary className="small">역할 · 할당 설정 (이 런에만 적용)</summary>
          {override && <RolesEditor roles={override.roles} onChange={(roles) => setOverride({ ...override, roles })}
            allocation={override.allocation} onAllocationChange={(allocation) => setOverride({ ...override, allocation })} />}
        </details>
      )}
      {five != null && five >= 0.6 && <p className="warn-text small">5시간 사용량이 {Math.round(five * 100)}%입니다. 80%에 도달하면 루프가 재개 시각까지 자동으로 기다립니다.</p>}
      <button className="primary" disabled={busy || !!busyRun} onClick={start}>{busyRun ? '다른 런 실행 중' : '▶ 런 시작'}</button>
      {msg && <p className="muted small">{msg}</p>}
    </div>
  );
}
