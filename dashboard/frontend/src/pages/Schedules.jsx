import React, { useState } from 'react';
import { api } from '../api.js';
import { useApi } from '../hooks.js';
import Icon from '../components/icons.jsx';
import { fmtTime } from '../labels.js';

const DAYS = ['월', '화', '수', '목', '금', '토', '일'];
const EMPTY = { name: '', spec: '', version: null, iterations: 5, dry_run: false, days: [0, 1, 2, 3, 4], start_time: '01:00', end_time: '06:00', enabled: true };

// 24시간 막대 위에 시간 창을 표시 — 자정을 넘는 창은 두 조각으로 그립니다.
function WindowBar({ start, end }) {
  const m = (t) => { const [h, mm] = t.split(':').map(Number); return h * 60 + mm; };
  const s = m(start), e = m(end);
  const segs = e > s ? [[s, e]] : [[s, 1440], [0, e]];
  const now = new Date(); const nm = now.getHours() * 60 + now.getMinutes();
  return (
    <div className="winbar" title={`${start} ~ ${end}`}>
      {segs.map(([a, b], i) => <span key={i} className="winbar-seg" style={{ left: `${(a / 1440) * 100}%`, width: `${((b - a) / 1440) * 100}%` }} />)}
      <span className="winbar-now" style={{ left: `${(nm / 1440) * 100}%` }} />
      <span className="winbar-tick" style={{ left: '25%' }}>6</span><span className="winbar-tick" style={{ left: '50%' }}>12</span><span className="winbar-tick" style={{ left: '75%' }}>18</span>
    </div>
  );
}

function Editor({ initial, specs, onSaved, onCancel }) {
  const [f, setF] = useState(initial);
  const [msg, setMsg] = useState('');
  const set = (patch) => setF({ ...f, ...patch });
  const save = async () => {
    try { await api('/api/schedules', { method: 'POST', body: { ...f, version: f.version || null } }); onSaved(); }
    catch (e) { setMsg(e.message); }
  };
  const spec = specs.find((s) => s.name === f.spec);
  return (
    <div className="card sched-editor">
      <div className="form-grid">
        <label className="field"><span>이름</span><input value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder="예: 야간 Astro 개선" /></label>
        <label className="field"><span>Spec</span>
          <select value={f.spec} onChange={(e) => set({ spec: e.target.value, version: null })}>
            <option value="">선택</option>
            {specs.map((s) => <option key={s.name} value={s.name}>{s.title || s.name}</option>)}
          </select>
        </label>
        <label className="field"><span>버전</span>
          <select value={f.version || ''} onChange={(e) => set({ version: e.target.value ? Number(e.target.value) : null })}>
            <option value="">실행 시점의 최신 (현재 v{spec?.version ?? '—'})</option>
            {spec && Array.from({ length: spec.version }, (_, i) => spec.version - i).map((v) => <option key={v} value={v}>v{v}</option>)}
          </select>
        </label>
        <label className="field"><span>반복</span><input inputMode="numeric" value={f.iterations} onChange={(e) => set({ iterations: Number(e.target.value.replace(/\D/g, '')) || 1 })} /></label>
      </div>
      <div className="field"><span>요일</span>
        <div className="day-picker">
          {DAYS.map((d, i) => (
            <button key={i} type="button" className={f.days.includes(i) ? 'active' : ''} onClick={() => set({ days: f.days.includes(i) ? f.days.filter((x) => x !== i) : [...f.days, i] })}>{d}</button>
          ))}
        </div>
      </div>
      <div className="form-grid">
        <label className="field"><span>시작</span><input type="time" value={f.start_time} onChange={(e) => set({ start_time: e.target.value })} /></label>
        <label className="field"><span>끝 (이때 '이번 반복 후 종료')</span><input type="time" value={f.end_time} onChange={(e) => set({ end_time: e.target.value })} /></label>
      </div>
      <WindowBar start={f.start_time} end={f.end_time} />
      <label className="check small"><input type="checkbox" checked={f.dry_run} onChange={(e) => set({ dry_run: e.target.checked })} />측정만 (사용량 없음)</label>
      {msg && <p className="error small">{msg}</p>}
      <div className="row-gap">
        <button className="primary" disabled={!f.spec || !f.days.length} onClick={save}>저장</button>
        <button className="ghost" onClick={onCancel}>취소</button>
      </div>
    </div>
  );
}

export default function Schedules() {
  const { data, error, reload } = useApi('/api/schedules', { pollMs: 60000 });
  const { data: specs } = useApi('/api/specs');
  const [editing, setEditing] = useState(null);
  if (error) return <p className="error">{error.message}</p>;
  if (!data || !specs) return <p className="muted">불러오는 중…</p>;
  const toggle = async (s) => { await api('/api/schedules', { method: 'POST', body: { ...s, enabled: !s.enabled } }); reload(); };
  const remove = async (s) => { if (window.confirm(`'${s.name}' 일정을 삭제할까요?`)) { await api(`/api/schedules/${s.id}`, { method: 'DELETE' }); reload(); } };

  return (
    <div className="stack narrow-page">
      <div className="row-between">
        <h1 className="run-title">일정</h1>
        {!editing && <button className="primary" onClick={() => setEditing(EMPTY)}><Icon name="plus" size={15} /> 새 일정</button>}
      </div>
      <p className="muted small">정한 요일·시간 창 안에서만 런을 자동으로 시작합니다. 창이 끝나면 '이번 반복 후 종료'를 보내고, 다른 런이 실행 중이면 건너뜁니다.</p>
      {editing && <Editor initial={editing} specs={specs} onSaved={() => { setEditing(null); reload(); }} onCancel={() => setEditing(null)} />}
      {data.length === 0 && !editing && <div className="card empty-state"><Icon name="calendar" size={28} /><div>예약된 일정이 없습니다</div></div>}
      <div className="card-grid-2">
      {data.map((s) => (
        <div key={s.id} className={`card sched-card ${s.enabled ? '' : 'off'}`}>
          <div className="row-between">
            <div><b>{s.name}</b><div className="muted small">{s.spec}{s.version ? ` v${s.version}` : ' (최신)'} · 반복 {s.iterations}{s.dry_run ? ' · 측정만' : ''}</div></div>
            <label className="switch" title={s.enabled ? '켜짐' : '꺼짐'}><input type="checkbox" checked={s.enabled} onChange={() => toggle(s)} /><span /></label>
          </div>
          <div className="day-picker readonly">{DAYS.map((d, i) => <span key={i} className={s.days.includes(i) ? 'active' : ''}>{d}</span>)}</div>
          <WindowBar start={s.start_time} end={s.end_time} />
          <div className="row-between wrap small">
            <span>{s.in_window ? <b className="good-text"><Icon name="clock" size={13} /> 지금 창 열림 (~{s.window_end?.slice(11)})</b> : <span className="muted">다음 시작 {s.next_start ? fmtTime(s.next_start) : '—'}</span>}</span>
            <span className="muted">{s.last_run_id ? <a href={`#/runs/${encodeURIComponent(s.last_run_id)}`}>최근 런 {fmtTime(s.last_started_at)}</a> : '아직 실행 안 됨'}</span>
          </div>
          <div className="row-gap">
            <button className="ghost small" onClick={() => setEditing(s)}>편집</button>
            <button className="ghost small danger" onClick={() => remove(s)}>삭제</button>
          </div>
        </div>
      ))}
      </div>
    </div>
  );
}
