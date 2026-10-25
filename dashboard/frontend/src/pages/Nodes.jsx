import React, { useState } from 'react';
import { api } from '../api.js';
import { useApi } from '../hooks.js';
import Icon from '../components/icons.jsx';
import { fmtTime } from '../labels.js';

// 실행 노드 — 홈서버(local)와 SSH로 붙는 다른 머신. 상태(연결·사양)는 오케스트레이터가 1분마다 확인합니다.
const EMPTY = { id: '', name: '', transport: 'ssh', host: '', port: 22, user: '', shell: 'powershell', os: 'windows', tags: [], workdir: '', enabled: true };

function NodeEditor({ initial, onDone }) {
  const [f, setF] = useState({ ...initial, tags: (initial.tags || []).join(', ') });
  const [msg, setMsg] = useState('');
  const set = (p) => setF({ ...f, ...p });
  const save = async () => {
    try {
      await api('/api/nodes', { method: 'POST', body: { ...f, port: Number(f.port) || 22, tags: f.tags.split(',').map((t) => t.trim()).filter(Boolean), workdir: f.workdir || null } });
      onDone();
    } catch (e) { setMsg(e.message); }
  };
  const isNew = !initial.id;
  return (
    <div className="card">
      <div className="form-grid">
        <label className="field"><span>ID</span><input className="mono" value={f.id} disabled={!isNew} onChange={(e) => set({ id: e.target.value })} placeholder="win-gpu" /></label>
        <label className="field"><span>이름</span><input value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder="Windows 데스크톱" /></label>
        <label className="field"><span>운영체제</span>
          <select value={f.os} onChange={(e) => set({ os: e.target.value, shell: e.target.value === 'windows' ? 'powershell' : 'bash' })}>
            <option value="windows">Windows</option><option value="linux">Linux / Raspberry Pi</option>
          </select>
        </label>
      </div>
      {f.transport === 'ssh' && (
        <div className="form-grid">
          <label className="field"><span>호스트 (LAN IP)</span><input className="mono" value={f.host} onChange={(e) => set({ host: e.target.value })} placeholder="192.168.0.20" /></label>
          <label className="field"><span>사용자</span><input className="mono" value={f.user} onChange={(e) => set({ user: e.target.value })} /></label>
          <label className="field"><span>포트</span><input inputMode="numeric" value={f.port} onChange={(e) => set({ port: e.target.value })} /></label>
        </div>
      )}
      <div className="form-grid">
        {f.os === 'windows' && (
          <label className="field"><span>SSH 기본 셸</span>
            <select value={f.shell} onChange={(e) => set({ shell: e.target.value })}>
              <option value="powershell">PowerShell (기본 셸을 바꾼 경우)</option>
              <option value="cmd">명령 프롬프트 (설치 기본값)</option>
            </select>
          </label>
        )}
        <label className="field"><span>태그 (쉼표로 구분)</span><input value={f.tags} onChange={(e) => set({ tags: e.target.value })} placeholder="gpu, cuda, windows" /></label>
        <label className="field"><span>작업 폴더</span><input className="mono" value={f.workdir || ''} onChange={(e) => set({ workdir: e.target.value })} placeholder={f.os === 'windows' ? 'C:/rsi-work' : '~/rsi-work'} /></label>
      </div>
      {msg && <p className="error small">{msg}</p>}
      <div className="row-gap">
        <button className="primary" onClick={save}>저장 후 연결 확인</button>
        <button className="ghost" onClick={onDone}>취소</button>
      </div>
    </div>
  );
}

function KeyGuide({ publicKey }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => { try { await navigator.clipboard.writeText(publicKey); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* 수동 복사 */ } };
  return (
    <details className="card key-guide">
      <summary><Icon name="shield" size={15} /> 노드에 홈서버 키 등록하는 방법</summary>
      <p className="small muted">홈서버는 이 전용 키로만 노드에 접속합니다. 노드에서 비밀번호 로그인은 꺼 두는 것을 권장합니다.</p>
      <div className="key-box"><code className="mono">{publicKey || '키 준비 중…'}</code><button className="small" onClick={copy}>{copied ? '복사됨' : '복사'}</button></div>
      <ol className="small steps-list">
        <li><b>Windows</b> (관리자 계정): 관리자 PowerShell에서
          <pre className="block-code">{`Add-Content -Path C:\\ProgramData\\ssh\\administrators_authorized_keys -Value '<위 공개키>'
icacls C:\\ProgramData\\ssh\\administrators_authorized_keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"`}</pre>
          일반 사용자 계정이면 <code>C:\Users\사용자\.ssh\authorized_keys</code>에 추가합니다.</li>
        <li><b>Linux / Raspberry Pi</b>: <code>~/.ssh/authorized_keys</code>에 위 공개키 한 줄을 추가합니다.</li>
        <li>노드를 저장하면 바로 연결을 확인합니다.</li>
      </ol>
    </details>
  );
}

export default function Nodes() {
  const { data, error, reload } = useApi('/api/nodes', { pollMs: 10000 });
  const [editing, setEditing] = useState(null);
  if (error) return <p className="error">{error.message}</p>;
  if (!data) return <p className="muted">불러오는 중…</p>;
  const check = async (n) => { await api(`/api/nodes/${n.id}/check`, { method: 'POST' }); setTimeout(reload, 3000); };
  const remove = async (n) => { if (window.confirm(`${n.name} 노드를 삭제할까요?`)) { await api(`/api/nodes/${n.id}`, { method: 'DELETE' }); reload(); } };

  return (
    <div className="stack">
      <div className="row-between">
        <h1 className="run-title">실행 노드</h1>
        {!editing && <button className="primary" onClick={() => setEditing(EMPTY)}><Icon name="plus" size={15} /> 노드 추가</button>}
      </div>
      <p className="muted small">평가를 다른 머신에서 돌리거나(GPU), 다른 OS에서도 통과하는지 확인할 때 씁니다. Spec의 <code>evaluation.node</code> · <code>evaluation.matrix</code>에서 노드 ID나 <code>tag:이름</code>으로 지정합니다.</p>
      {editing && <NodeEditor initial={editing} onDone={() => { setEditing(null); setTimeout(reload, 2500); }} />}
      <div className="card-grid-2">
      {data.nodes.map((n) => {
        const st = n.online == null ? 'unknown' : n.online ? 'on' : 'off';
        return (
          <div key={n.id} className={`card node-card ${n.enabled ? '' : 'off'}`}>
            <div className="row-between">
              <div className="row-gap">
                <span className={`node-dot ${st}`} title={st === 'on' ? '연결됨' : st === 'off' ? '연결 안 됨' : '확인 전'} />
                <b>{n.name}</b><span className="muted small mono">{n.id}</span>
              </div>
              <span className={`badge ${st === 'on' ? 'good' : st === 'off' ? 'danger' : 'neutral'}`}>
                <Icon name={st === 'on' ? 'check' : st === 'off' ? 'x' : 'clock'} size={12} /> {st === 'on' ? '연결됨' : st === 'off' ? '연결 안 됨' : '확인 전'}
              </span>
            </div>
            <div className="muted small mono">{n.transport === 'local' ? '홈서버 (로컬 실행)' : `${n.user ? n.user + '@' : ''}${n.host}:${n.port}`} · {n.os === 'windows' ? 'Windows' : 'Linux'}</div>
            {n.info && Object.keys(n.info).length > 0 && (
              <div className="spec-chips">
                {n.info.os && <span className="chip-static">{n.info.os}</span>}
                {n.info.arch && <span className="chip-static">{n.info.arch}</span>}
                {n.info.cpu && <span className="chip-static">CPU {n.info.cpu}</span>}
                {n.info.mem_mb && <span className="chip-static">RAM {Math.round(n.info.mem_mb / 1024)}GB</span>}
                {n.info.gpu && <span className="chip-static gpu">GPU {n.info.gpu}</span>}
                {n.info.tools && n.info.tools.split(' ').filter(Boolean).map((t) => <span key={t} className="chip-static tool">{t}</span>)}
              </div>
            )}
            {n.error && st === 'off' && <p className="error small clamp-2">{n.error}</p>}
            <div className="row-between wrap">
              <div className="row-gap">{n.tags.map((t) => <span key={t} className="badge neutral">#{t}</span>)}</div>
              <span className="muted small">{n.last_check ? `확인 ${fmtTime(n.last_check)}` : ''}</span>
            </div>
            <div className="row-gap">
              <button className="ghost small" onClick={() => check(n)}><Icon name="plug" size={13} /> 연결 확인</button>
              {n.transport === 'ssh' && <button className="ghost small" onClick={() => setEditing(n)}>편집</button>}
              {n.id !== 'server' && <button className="ghost small danger" onClick={() => remove(n)}>삭제</button>}
            </div>
          </div>
        );
      })}
      </div>
      <KeyGuide publicKey={data.public_key} />
    </div>
  );
}
