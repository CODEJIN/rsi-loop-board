import React, { useState } from 'react';
import { api } from '../api.js';
import { useApi } from '../hooks.js';
import { Badge } from '../components/common.jsx';
import { fmtTime } from '../labels.js';

const STATUS = { idle: ['대기', 'neutral'], thinking: ['답변 중', 'info'], error: ['오류', 'danger'] };

export default function DesignList() {
  const { data, error } = useApi('/api/design', { pollMs: 10000 });
  const [form, setForm] = useState({ title: '', repo: '', message: '' });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const start = async () => {
    if (!window.confirm('설계 대화는 구독 사용량을 씁니다(메시지마다 Claude Code 실행). 시작할까요?')) return;
    setBusy(true); setMsg('');
    try {
      const r = await api('/api/design', { method: 'POST', body: form });
      window.location.hash = `#/design/${r.id}`;
    } catch (e) { setMsg(e.message); }
    setBusy(false);
  };

  return (
    <div className="stack">
      <h1 className="run-title">설계</h1>
      <p className="muted small">대화로 개선 대상·지표·판정 방법을 정하고 Spec 초안을 만듭니다. 설계 도우미는 대상 저장소를 읽기만 하고 파일을 바꾸지 않습니다.</p>
      <section className="card">
        <h2>새 설계 대화</h2>
        <div className="form-grid">
          <label className="field"><span>제목</span><input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="예: ETF Manager 리밸런싱 정확도" /></label>
          <label className="field"><span>대상 저장소 (절대 경로)</span><input className="mono" value={form.repo} onChange={(e) => setForm({ ...form, repo: e.target.value })} placeholder="/home/heejo/ETF_Manager" /></label>
        </div>
        <label className="field"><span>무엇을 개선하고 싶은지</span>
          <textarea rows={3} value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })}
            placeholder="예: 대시보드 첫 화면 로딩이 느려. 어떤 지표로 개선하면 좋을지 같이 정해보자." /></label>
        <button className="primary" disabled={busy || !form.repo || !form.message} onClick={start}>대화 시작</button>
        {msg && <p className="error small">{msg}</p>}
      </section>
      <section className="card">
        <h2>대화 목록</h2>
        {error && <p className="error">{error.message}</p>}
        {!data ? <p className="muted">불러오는 중…</p> : data.length === 0 ? <p className="muted small">아직 없습니다.</p> : (
          <div className="run-list">
            {data.map((s) => (
              <a key={s.id} className="run-item" href={`#/design/${s.id}`}>
                <div className="run-main"><b>{s.title}</b><div className="muted small mono">{s.repo}</div></div>
                <span className="muted small">메시지 {s.messages} · {fmtTime(s.updated_at)}</span>
                <Badge tone={(STATUS[s.status] || [])[1] || 'neutral'}>{(STATUS[s.status] || [s.status])[0]}</Badge>
              </a>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
