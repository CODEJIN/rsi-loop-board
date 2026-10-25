import React, { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import { useApi } from '../hooks.js';
import { Badge } from '../components/common.jsx';
import Markdown from '../components/Markdown.jsx';
import Icon from '../components/icons.jsx';
import { SpecBoard, Readiness, ActionCards, ActionLog } from '../components/DesignBoard.jsx';
import { COMMAND_STATUS, fmtTime } from '../labels.js';

// 채팅 말풍선에서는 보드 블록을 숨깁니다(답변 중 아직 닫히지 않은 블록 포함).
const BOARD_BLOCK = /```rsi-(board|actions)\n[\s\S]*?(```|$)/g;
const CMD_LABEL = { create_workspace: '작업 공간', save_eval_script: '스크립트 저장', init_repo: 'git 초기화', save_spec: 'Spec 저장', start_run: '측정만 런', repo_check: '원본 확인' };
const hasBoard = (t) => /```rsi-board\n/.test(t || '');

export default function DesignSession({ id }) {
  const [thinking, setThinking] = useState(false);
  // 답변 중에는 1.5초마다, 아니면 10초마다 갱신 — 답변은 오케스트레이터가 DB에 조금씩 채워 넣습니다.
  const { data, error, reload } = useApi(`/api/design/${id}`, { pollMs: thinking ? 1500 : 10000 });
  const { data: orch } = useApi('/api/orchestrator', { pollMs: 30000 });
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [pane, setPane] = useState('chat');   // 좁은 화면에서만 의미 있음: 대화 ↔ 보드
  const bottom = useRef(null);
  const input = useRef(null);

  useEffect(() => { setThinking(data?.session?.status === 'thinking'); }, [data?.session?.status]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'end' }); }, [data?.messages?.length, data?.messages?.at?.(-1)?.text?.length, pane]);

  if (error) return <p className="error">{error.message}</p>;
  if (!data) return <p className="muted">불러오는 중…</p>;
  const { session, messages, board, readiness, scripts, commands } = data;
  const decided = board ? Object.values(board.status).filter((v) => v === 'decided').length : 0;
  const ready = readiness.filter((r) => r.ok).length;

  const send = async () => {
    setBusy(true); setMsg('');
    try { await api(`/api/design/${id}/messages`, { method: 'POST', body: { text } }); setText(''); setThinking(true); reload(); }
    catch (e) { setMsg(e.message); }
    setBusy(false);
  };
  const ask = (q) => {
    setText((t) => `${t ? `${t}\n` : ''}> ${q}\n`);
    setPane('chat');
    setTimeout(() => input.current?.focus(), 0);
  };

  return (
    <div className="stack">
      <a href="#/design" className="muted small">← 설계 목록</a>
      <div className="row-between wrap">
        <div>
          <h1 className="run-title">{session.title}</h1>
          <div className="muted small mono">{session.repo}</div>
        </div>
        <Badge tone={session.status === 'thinking' ? 'info' : session.status === 'error' ? 'danger' : 'neutral'}>
          {session.status === 'thinking' ? '답변 중' : session.status === 'error' ? `오류: ${session.last_error || ''}` : '대기'}
        </Badge>
      </div>

      <div className="segmented design-tabs" role="tablist">
        <button type="button" className={pane === 'chat' ? 'active' : ''} onClick={() => setPane('chat')}><Icon name="chat" size={14} /> 대화</button>
        <button type="button" className={pane === 'board' ? 'active' : ''} onClick={() => setPane('board')}>
          <Icon name="file" size={14} /> 보드 <span className="muted small">{decided}/7 · 준비 {ready}/{readiness.length}</span>
        </button>
      </div>

      <section className="grid-main design-grid">
        <div className={`card chat ${pane === 'chat' ? '' : 'pane-off'}`}>
          <div className="chat-log">
            {messages.map((m) => (
              <div key={m.id} className={`bubble ${m.role}`}>
                <div className="bubble-head muted small">{m.role === 'user' ? '나' : '설계 도우미'} · {fmtTime(m.ts)}
                  {m.meta?.tools ? ` · 파일 탐색 ${m.meta.tools}회` : ''}</div>
                {m.text ? <Markdown text={m.text.replace(BOARD_BLOCK, '').replace(/\n{3,}/g, '\n\n').trimEnd()} /> : null}
                {m.role === 'assistant' && hasBoard(m.text) && !m.meta?.streaming && (
                  <button type="button" className="board-chip" onClick={() => setPane('board')}><Icon name="check" size={12} /> 보드 갱신</button>
                )}
                {m.meta?.actions?.length > 0 && <ActionLog actions={m.meta.actions} />}
                {m.actions && <ActionCards sid={id} actions={m.actions} session={session} reload={reload} fetchSession={() => api(`/api/design/${id}`)} />}
                {m.meta?.streaming && <div className="muted small"><span className="pulse" />{m.meta.last_tool ? `살펴보는 중: ${m.meta.last_tool}` : '생각 중…'}</div>}
              </div>
            ))}
            <div ref={bottom} />
          </div>
          <div className="inject">
            <textarea ref={input} rows={3} value={text} disabled={thinking} onChange={(e) => setText(e.target.value)}
              placeholder={thinking ? '답변을 기다리는 중…' : '답장 (Ctrl+Enter로 보내기)'}
              onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && text.trim()) send(); }} />
            <button className="primary" disabled={busy || thinking || !text.trim()} onClick={send}>보내기</button>
          </div>
          {msg && <p className="muted small">{msg}</p>}
        </div>

        <div className={`stack ${pane === 'board' ? '' : 'pane-off'}`}>
          <SpecBoard board={board} onAsk={ask} />
          <Readiness id={id} items={readiness} scripts={scripts} session={session} orchestrator={orch} onDone={reload} />
          {commands.length > 0 && (
            <div className="card">
              <h2>요청 결과</h2>
              <ul className="commands">{commands.map((c) => (
                <li key={c.id}><span className="small">{CMD_LABEL[c.type] || c.type}</span>
                  <Badge tone={COMMAND_STATUS[c.status]?.tone}>{COMMAND_STATUS[c.status]?.label || c.status}</Badge>
                  {c.result && <span className="muted small">{c.result}</span>}</li>
              ))}</ul>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
