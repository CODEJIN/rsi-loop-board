import React, { useState } from 'react';
import { api } from '../api.js';
import { useApi } from '../hooks.js';
import { StatusBadge } from '../components/common.jsx';
import Icon from '../components/icons.jsx';
import { DecisionDots, Sparkline } from '../components/viz.jsx';
import { fmtTime, fmtValue, gainPct } from '../labels.js';

const STATUS_FILTERS = [['', '전체'], ['running', '실행 중'], ['finished', '완료'], ['aborted', '중단'], ['dry', '측정만']];

// 런 관리 — 필터·보관·삭제(휴지통)·비교 선택(최대 3개)
export default function Runs({ lastEvent }) {
  const [status, setStatus] = useState('');
  const [spec, setSpec] = useState('');
  const [archived, setArchived] = useState('0');
  const [picked, setPicked] = useState([]);
  const [menu, setMenu] = useState(null);
  const q = new URLSearchParams({ archived, ...(status ? { status } : {}), ...(spec ? { spec } : {}) }).toString();
  const { data, error, reload } = useApi(`/api/runs?${q}`, { refreshKey: lastEvent, shouldRefresh: (ev) => ['run_start', 'run_end', 'run_aborted', 'judgment'].includes(ev?.type) });
  const { data: specs } = useApi('/api/specs');
  if (error) return <p className="error">{error.message}</p>;

  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length >= 3 ? p : [...p, id]));
  const archive = async (r) => { setMenu(null); await api(`/api/runs/${encodeURIComponent(r.id)}/meta`, { method: 'POST', body: { archived: !r.archived } }); reload(); };
  const remove = async (r) => {
    setMenu(null);
    if (!window.confirm(`${r.title || r.id} (${fmtTime(r.started_at)}) 런을 삭제할까요? 기록은 서버의 runs/_trash 폴더로 옮겨집니다.`)) return;
    try { await api(`/api/runs/${encodeURIComponent(r.id)}`, { method: 'DELETE' }); setPicked((p) => p.filter((x) => x !== r.id)); reload(); }
    catch (e) { window.alert(e.message); }
  };

  return (
    <div className="stack">
      <div className="row-between">
        <h1 className="run-title">런</h1>
        <span className="muted small">{data ? `${data.length}개` : ''}</span>
      </div>
      <div className="filters">
        <div className="chips">
          {STATUS_FILTERS.map(([v, l]) => <button key={v} className={`chip ${status === v ? 'active' : ''}`} onClick={() => setStatus(v)}>{l}</button>)}
        </div>
        <div className="row-gap">
          <select value={spec} onChange={(e) => setSpec(e.target.value)} className="select-sm">
            <option value="">모든 Spec</option>
            {(specs || []).map((s) => <option key={s.name} value={s.name}>{s.title || s.name}</option>)}
          </select>
          <button className={`chip ${archived !== '0' ? 'active' : ''}`} onClick={() => setArchived(archived === '0' ? 'all' : '0')}>
            <Icon name="inbox" size={13} /> 보관 포함
          </button>
        </div>
      </div>

      {!data ? <p className="muted">불러오는 중…</p> : data.length === 0 ? (
        <div className="card empty-state"><Icon name="file" size={28} /><div>조건에 맞는 런이 없습니다</div></div>
      ) : (
        <div className="card run-cards">
          {data.map((r) => {
            const pct = gainPct(r.baseline_objective, r.best_objective, r.metric);
            const sel = picked.includes(r.id);
            return (
              <div key={r.id} className={`run-row managed ${sel ? 'picked' : ''} ${r.archived ? 'archived' : ''}`}>
                <label className="pick" title="비교에 추가 (최대 3개)">
                  <input type="checkbox" checked={sel} disabled={!sel && picked.length >= 3} onChange={() => toggle(r.id)} />
                </label>
                <a className="run-row-main" href={`#/runs/${encodeURIComponent(r.id)}`}>
                  <div className="row-gap"><b className="ellipsis">{r.title || r.spec || r.id}</b>{r.spec_version && <span className="muted small">v{r.spec_version}</span>}</div>
                  <div className="muted small">{fmtTime(r.started_at)}{r.dry_run ? ' · 측정만' : ''}{r.archived ? ' · 보관됨' : ''}</div>
                </a>
                <Sparkline series={r.series} baseline={r.baseline_objective} metric={r.metric} />
                <div className="run-row-num">
                  <b>{fmtValue(r.best_objective, r.metric)}</b>
                  {pct ? <span className={`small ${pct > 0 ? 'good-text' : 'muted'}`}>{pct > 0 ? '개선' : '악화'} {Math.abs(pct)}%</span> : <span className="small muted">—</span>}
                </div>
                <div className="run-row-dots"><DecisionDots series={r.series} /></div>
                <div className="row-actions">
                  <StatusBadge status={r.status} />
                  <button className="ghost icon-only" onClick={() => setMenu(menu === r.id ? null : r.id)} aria-label="더보기"><Icon name="more" size={16} /></button>
                  {menu === r.id && (
                    <div className="popover">
                      <button onClick={() => archive(r)}><Icon name="inbox" size={15} />{r.archived ? '보관 해제' : '보관'}</button>
                      <button className="danger" disabled={r.status === 'running' || r.status === 'paused'} onClick={() => remove(r)}><Icon name="x" size={15} />삭제</button>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {picked.length > 0 && (
        <div className="compare-bar">
          <span className="small">비교 {picked.length}/3</span>
          <button className="ghost small" onClick={() => setPicked([])}>선택 해제</button>
          <button className="primary" disabled={picked.length < 2} onClick={() => { window.location.hash = `#/compare/${picked.map(encodeURIComponent).join(',')}`; }}>
            <Icon name="trend" size={15} /> 비교하기
          </button>
        </div>
      )}
    </div>
  );
}
