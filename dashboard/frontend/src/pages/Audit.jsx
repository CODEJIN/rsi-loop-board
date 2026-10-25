import React, { useState } from 'react';
import { useApi } from '../hooks.js';
import Icon from '../components/icons.jsx';
import { COMMAND, fmtTime } from '../labels.js';

// 감사 로그 — 상태를 바꾼 요청(누가·언제·어디서·무엇을·결과). 비밀번호·OTP·본문은 서버에서 가린 채 저장됩니다.
const CATS = [['', '전체'], ['command', '명령'], ['auth', '로그인'], ['spec', 'Spec'], ['run', '런'], ['schedule', '일정'], ['settings', '설정'], ['design', '설계']];
const CAT_ICON = { command: 'bolt', auth: 'user', spec: 'file', run: 'inbox', schedule: 'calendar', settings: 'settings', design: 'chat', other: 'more' };

function summary(r) {
  const d = r.detail || {};
  if (r.category === 'command') {
    const label = COMMAND[d.type]?.label || d.type;
    const target = d.run_id ? ` · ${d.run_id}` : d.payload?.spec ? ` · ${d.payload.spec}${d.payload.version ? ` v${d.payload.version}` : ''}` : '';
    const extra = d.payload?.iteration ? ` #${d.payload.iteration}` : d.payload?.text ? ` “${d.payload.text}”` : '';
    return `${target}${extra}`.replace(/^ · /, '') || label;   // 제목에 명령 이름이 있으므로 대상만
  }
  if (r.category === 'spec') return d.note || '새 버전';
  if (r.category === 'schedule') return d.name || r.path.split('/').pop();
  if (r.category === 'run') return r.path.split('/')[3] + (d.archived !== undefined ? (d.archived ? ' 보관' : ' 보관 해제') : '');
  if (r.category === 'design') return d.title || d.text || d.filename || d.name || '';
  return '';
}

export default function Audit() {
  const [cat, setCat] = useState('');
  const [failed, setFailed] = useState(false);
  const q = new URLSearchParams({ ...(cat ? { category: cat } : {}), ...(failed ? { failed: 'true' } : {}) }).toString();
  const { data, error } = useApi(`/api/audit?${q}`, { pollMs: 30000 });
  if (error) return <p className="error">{error.message}</p>;

  return (
    <div className="stack">
      <div className="row-between">
        <h1 className="run-title">감사 로그</h1>
        <button className={`chip ${failed ? 'active' : ''}`} onClick={() => setFailed((v) => !v)}><Icon name="alert" size={13} /> 실패만</button>
      </div>
      <div className="chips">
        {CATS.map(([v, l]) => <button key={v} className={`chip ${cat === v ? 'active' : ''}`} onClick={() => setCat(v)}>{l}</button>)}
      </div>
      {!data ? <p className="muted">불러오는 중…</p> : data.length === 0 ? (
        <div className="card empty-state"><Icon name="shield" size={28} /><div>기록이 없습니다</div></div>
      ) : (
        <div className="card audit-list">
          {data.map((r) => {
            const ok = r.status != null && r.status < 400;
            return (
              <div key={r.id} className={`audit-row ${ok ? '' : 'fail'}`}>
                <span className={`audit-icon c-${r.category}`}><Icon name={CAT_ICON[r.category] || 'more'} size={15} /></span>
                <div className="audit-main">
                  <div className="row-gap"><b>{r.category === 'command' ? `명령 · ${COMMAND[r.detail?.type]?.label || r.detail?.type}` : r.action}</b>{!ok && <span className="badge danger">{r.status ?? '오류'}</span>}</div>
                  {summary(r) && <div className="small muted ellipsis">{summary(r)}</div>}
                </div>
                <div className="audit-meta small muted">
                  <span>{fmtTime(r.ts)}</span>
                  <span title={r.ip}>{r.device} · {r.ip}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
