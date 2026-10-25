import React, { useEffect, useState } from 'react';
import { api } from '../api.js';

// 모델 계층 설정 — 역할별 계층을 실제 모델에 연결합니다(런 시작 시 오케스트레이터가 Spec 스냅샷에 합침).
function ModelTiers() {
  const [cfg, setCfg] = useState(null);
  const [msg, setMsg] = useState('');
  useEffect(() => { api('/api/settings/models').then(setCfg).catch((e) => setMsg(e.message)); }, []);
  if (!cfg) return <section className="card"><h2>모델 계층</h2><p className="muted small">{msg || '불러오는 중…'}</p></section>;
  const set = (group, k, v) => setCfg({ ...cfg, [group]: { ...cfg[group], [k]: v } });
  const save = async () => {
    try { setCfg(await api('/api/settings/models', { method: 'POST', body: cfg })); setMsg('저장했습니다. 새로 시작하는 런부터 적용됩니다.'); }
    catch (e) { setMsg(e.message); }
  };
  return (
    <section className="card">
      <h2>모델 계층</h2>
      <p className="muted small">역할별 계층(상위·표준·경량)을 실제 모델에 연결합니다. Claude Code 모델 별칭(opus, sonnet, haiku) 또는 전체 ID를 쓰고, 비우면 기본 모델을 씁니다.
        지정한 모델을 플랜에서 쓸 수 없으면 루프가 기본 모델로 한 번 다시 실행하고 경고를 남깁니다.</p>
      <table className="roles-table">
        <thead><tr><th>계층</th><th>모델</th><th>가중치 (사용량 추정용)</th></tr></thead>
        <tbody>
          {[['high', '상위'], ['standard', '표준'], ['light', '경량']].map(([k, l]) => (
            <tr key={k}>
              <td>{l}</td>
              <td><input className="mono" value={cfg.model_tiers[k] || ''} placeholder="기본 모델" onChange={(e) => set('model_tiers', k, e.target.value)} /></td>
              <td><input inputMode="decimal" value={cfg.tier_factor[k]} onChange={(e) => set('tier_factor', k, e.target.value)} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <label className="field"><span>설계 대화에 쓸 계층 (긴 답변은 출력 토큰이 많아 Pro에서는 표준 권장)</span>
        <select value={cfg.design_tier} onChange={(e) => setCfg({ ...cfg, design_tier: e.target.value })}>
          <option value="high">상위</option><option value="standard">표준</option><option value="light">경량</option>
        </select>
      </label>
      <button className="primary" onClick={save}>저장</button>
      {msg && <p className="muted small">{msg}</p>}
    </section>
  );
}

// 텔레그램 알림 설정 — 봇 토큰은 서버 .env에, chat_id와 알림 종류는 여기서 저장합니다.
export default function Settings() {
  const [cfg, setCfg] = useState(null);
  const [chats, setChats] = useState(null);
  const [chatInput, setChatInput] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);

  const load = () => api('/api/settings/telegram').then((c) => { setCfg(c); setChatInput(c.chat_id || ''); }).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);

  const run = async (fn, okMsg) => {
    setBusy(true); setMsg('');
    try { await fn(); if (okMsg) setMsg(okMsg); } catch (e) { setMsg(e.message); }
    setBusy(false);
  };
  const save = (body, okMsg) => run(async () => setCfg(await api('/api/settings/telegram', { method: 'POST', body })), okMsg);

  if (!cfg) return <p className="muted">{msg || '불러오는 중…'}</p>;
  return (
    <div className="card-grid-2">
      <ModelTiers />
      <section className="card">
        <h2>텔레그램 알림</h2>
        {!cfg.token_configured ? (
          <div className="steps">
            <p>봇 토큰이 아직 설정되지 않았습니다.</p>
            <ol className="small">
              <li>텔레그램에서 <b>@BotFather</b>에게 <code>/newbot</code>을 보내 봇을 만들고 토큰을 받습니다.</li>
              <li>서버의 <code>~/RSI_Loop/dashboard/.env</code>에 <code>TELEGRAM_BOT_TOKEN=받은토큰</code>을 추가합니다.</li>
              <li><code>docker compose up -d</code>로 대시보드를 다시 띄우고 이 화면을 새로고침합니다.</li>
            </ol>
          </div>
        ) : (
          <>
            <div className="label">받을 대화 (chat_id)</div>
            <p className="muted small">만든 봇에게 텔레그램에서 아무 메시지나 보낸 뒤 “대화 찾기”를 누르세요.</p>
            <div className="row-gap wrap-gap">
              <input value={chatInput} onChange={(e) => setChatInput(e.target.value)} placeholder="chat_id" style={{ maxWidth: 220 }} />
              <button disabled={busy} onClick={() => run(async () => setChats(await api('/api/settings/telegram/discover', { method: 'POST' })))}>대화 찾기</button>
              <button className="primary" disabled={busy || !chatInput} onClick={() => save({ chat_id: chatInput }, '저장했습니다.')}>저장</button>
              <button disabled={busy || !cfg.chat_id} onClick={() => run(() => api('/api/settings/telegram/test', { method: 'POST' }), '테스트 메시지를 보냈습니다.')}>테스트 전송</button>
            </div>
            {chats && (chats.length === 0
              ? <p className="muted small">최근 메시지가 없습니다. 봇에게 메시지를 보낸 뒤 다시 눌러주세요.</p>
              : <ul className="chat-list">{chats.map((c) => (
                <li key={c.id}><button className="ghost small" onClick={() => setChatInput(String(c.id))}>선택</button> {c.name} <span className="muted small mono">{c.id} · {c.type}</span></li>
              ))}</ul>)}

            <div className="label" style={{ marginTop: 16 }}>알림 종류</div>
            <div className="toggles">
              {Object.entries(cfg.event_types).map(([key, label]) => (
                <label key={key} className="check">
                  <input type="checkbox" checked={!!cfg.events[key]} disabled={busy}
                    onChange={(e) => save({ events: { ...cfg.events, [key]: e.target.checked } })} />
                  {label}
                </label>
              ))}
            </div>
            {!cfg.public_url && <p className="warn-text small">PUBLIC_URL이 설정되지 않아 알림에 대시보드 링크가 붙지 않습니다.</p>}
          </>
        )}
        {msg && <p className="muted small">{msg}</p>}
      </section>
    </div>
  );
}
