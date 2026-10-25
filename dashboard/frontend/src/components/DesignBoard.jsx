import React, { useState } from 'react';
import { api } from '../api.js';
import Icon from './icons.jsx';

// 설계 화면 오른쪽 — 대화에서 누적된 Spec을 섹션 카드로(보드), 실행까지 남은 단계를 체크리스트로 보여줍니다.
const SECTIONS = [
  ['target', 'target', '개선 대상'], ['metric', 'trend', '지표'], ['gates', 'shield', '정확성 검사'],
  ['judge', 'gavel', '판정'], ['budget', 'gauge', '예산'], ['evaluation', 'beaker', '평가 방법'], ['workspace', 'folder', '작업 공간'],
];
const STATUS = {
  decided: { icon: 'check', label: '확정', cls: 'good' },
  proposed: { icon: 'half', label: '제안', cls: 'info' },
  open: { icon: 'circle', label: '미정', cls: 'neutral' },
};
const JUDGE = [['auto', '자동'], ['hybrid', '혼합'], ['human', '사람']];

function ProgressRing({ done, total, size = 58 }) {
  const r = 22, c = 2 * Math.PI * r, pct = total ? done / total : 0;
  return (
    <svg viewBox="0 0 50 50" width={size} height={size} role="img" aria-label={`${total}개 중 ${done}개 확정`}>
      <circle cx="25" cy="25" r={r} className="ring-track" />
      <circle cx="25" cy="25" r={r} className="ring-fill st-good" strokeDasharray={`${pct * c} ${c}`} transform="rotate(-90 25 25)" />
      <text x="25" y="29" className="pring-text">{done}/{total}</text>
    </svg>
  );
}

const Empty = () => <span className="muted small">아직 정해지지 않음</span>;
const pct = (v) => (v == null ? '—' : `${Math.round(v * 100)}%`);

function SectionBody({ k, spec, gates }) {
  const t = spec.target || {}, m = spec.metric || {}, j = spec.judge || {}, b = spec.budget || {}, e = spec.evaluation || {}, w = spec.workspace || {};
  switch (k) {
    case 'target':
      return t.goal || t.editable_paths ? (
        <>
          {t.goal && <p className="board-text">{t.goal}</p>}
          <div className="chip-row">
            {t.type && <span className="chip-static tool">{t.type}</span>}
            {(t.editable_paths || []).map((p) => <span key={p} className="chip-static mono">{p}</span>)}
          </div>
        </>
      ) : <Empty />;
    case 'metric':
      return m.name ? (
        <div className="board-metric">
          <span className="board-metric-name">{m.name}</span>
          {m.direction && <span className="chip-static">{m.direction === 'minimize' ? '↓ 낮을수록 좋음' : '↑ 높을수록 좋음'}</span>}
          {m.unit && <span className="chip-static mono">{m.unit}</span>}
          <span className={`chip-static ${m.noise_threshold == null ? 'warn-chip' : ''}`}>잡음 기준 {m.noise_threshold ?? '미정'}</span>
        </div>
      ) : <Empty />;
    case 'gates':
      return gates.length ? (
        <ul className="board-gates">
          {gates.map((g, i) => <li key={i}><Icon name="shield" size={13} /><span><b>{g.name}</b>{g.detail && <span className="muted small"> — {g.detail}</span>}</span></li>)}
        </ul>
      ) : <Empty />;
    case 'judge':
      return (
        <>
          <div className="segmented readonly" aria-label="판정 정책">
            {JUDGE.map(([v, l]) => <span key={v} className={(j.policy || '') === v ? 'active' : ''}>{l}</span>)}
          </div>
          {j.policy === 'hybrid' && j.hybrid && <div className="muted small">개선 {j.hybrid.improvement_pct_gte ?? '—'}% 이상 또는 {j.hybrid.diff_lines_gte ?? '—'}줄 이상 변경이면 사람 승인</div>}
        </>
      );
    case 'budget':
      return b.max_iterations || b.stop_if_five_hour_utilization_gte ? (
        <div className="board-stats">
          <div><div className="stat-label">반복</div><div className="stat-value">{b.max_iterations ?? '—'}</div></div>
          <div><div className="stat-label">5시간 한도</div><div className="stat-value">{pct(b.stop_if_five_hour_utilization_gte)}</div></div>
          <div><div className="stat-label">7일 한도</div><div className="stat-value">{pct(b.stop_if_seven_day_utilization_gte)}</div></div>
        </div>
      ) : <Empty />;
    case 'evaluation':
      return e.command ? (
        <>
          <code className="board-cmd">{e.command}</code>
          <div className="chip-row">
            {e.timeout_min && <span className="chip-static"><Icon name="clock" size={11} /> {e.timeout_min}분</span>}
            {e.node && <span className="chip-static"><Icon name="plug" size={11} /> {e.node}</span>}
            {(e.matrix || []).map((x, i) => <span key={i} className="chip-static"><Icon name="plug" size={11} /> {x.name || x.node}</span>)}
          </div>
          {e.stages?.length > 0 && <div className="stage-flow small">{e.stages.map((st, i) => <React.Fragment key={st.name || i}>{i > 0 && <Icon name="chevron" size={12} className="muted" />}<span className="chip-static">{st.name}</span></React.Fragment>)}</div>}
          {e.description && <p className="muted small clamp-2">{e.description}</p>}
        </>
      ) : <Empty />;
    case 'workspace':
      return w.repo ? <code className="board-cmd">{w.repo}</code> : <Empty />;
    default:
      return null;
  }
}

export function SpecBoard({ board, onAsk }) {
  if (!board) {
    return (
      <div className="card board">
        <h2>Spec 보드</h2>
        <p className="muted small">대화가 진행되면 정해진 내용이 여기에 섹션별로 채워집니다.</p>
      </div>
    );
  }
  const decided = SECTIONS.filter(([k]) => board.status[k] === 'decided').length;
  return (
    <div className="card board">
      <div className="board-head">
        <ProgressRing done={decided} total={SECTIONS.length} />
        <div>
          <h2>{board.spec.title || board.spec.spec || 'Spec 보드'}</h2>
          <div className="muted small mono">{board.spec.spec || '이름 미정'}</div>
          <div className="board-legend small">
            {Object.entries(STATUS).map(([k, s]) => <span key={k} className={`st-${s.cls}`}><Icon name={s.icon} size={12} /> {s.label}</span>)}
          </div>
        </div>
      </div>

      {board.questions.length > 0 && (
        <div className="board-questions">
          <div className="stat-label">답이 필요한 질문</div>
          {board.questions.map((q, i) => (
            <button key={i} type="button" className="question" onClick={() => onAsk(q)} title="답장 입력란에 인용">
              <Icon name="help" size={14} /><span>{q}</span>
            </button>
          ))}
        </div>
      )}

      <ol className="board-sections">
        {SECTIONS.map(([k, icon, label]) => {
          const st = STATUS[board.status[k]] || STATUS.open;
          return (
            <li key={k} className={`board-sec s-${board.status[k] || 'open'}`}>
              <div className="board-sec-head">
                <span className="row-gap"><Icon name={icon} size={15} /><b>{label}</b></span>
                <span className={`badge ${st.cls}`}><Icon name={st.icon} size={11} /> {st.label}</span>
              </div>
              <SectionBody k={k} spec={board.spec} gates={board.gates} />
            </li>
          );
        })}
      </ol>

      {board.parse_errors?.length > 0 && <p className="warn-text small"><Icon name="alert" size={13} /> {board.parse_errors.at(-1)}</p>}
      <details className="block">
        <summary className="small">YAML 보기</summary>
        <pre className="diff draft">{board.yaml}</pre>
        <button className="small ghost" onClick={() => {
          try { sessionStorage.setItem('rsi-spec-draft', board.yaml); } catch { /* 저장 불가 환경이면 이동만 */ }
          window.location.hash = '#/specs/new';
        }}><Icon name="code" size={13} /> Spec 편집기에서 직접 고치기</button>
      </details>
    </div>
  );
}

// 실행 준비 — 각 단계가 채워지면 다음 행동(저장·측정·시작)이 같은 자리에 나타납니다.
export function Readiness({ id, items, scripts, session, orchestrator, onDone }) {
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState('');
  const [folder, setFolder] = useState('');
  const [wsName, setWsName] = useState('');
  const [gitignore, setGitignore] = useState(null);
  const by = Object.fromEntries(items.map((i) => [i.key, i]));
  const done = items.filter((i) => i.ok).length;
  const run = async (key, fn) => {
    setBusy(key); setMsg('');
    try { setMsg(await fn()); onDone(); } catch (e) { setMsg(e.message); }
    setBusy('');
  };
  const sc = by.scripts;
  const dir = folder || sc.folder || '';
  const draftOf = (f) => scripts.find((s) => s.filename === f);
  const saveScript = (f, exists) => run(`script:${f}`, async () => {
    if (exists && !window.confirm(`evals/${dir}/${f} 파일이 이미 있습니다. 대화의 초안으로 덮어쓸까요?`)) return '';
    const s = draftOf(f);
    const r = await api(`/api/design/${id}/save-script`, { method: 'POST', body: { folder: dir, filename: s.filename, content: s.content.replace(/^.*\n/, '') } });
    return `${r.path} 저장 요청을 보냈습니다.`;
  });
  const wsGuess = by.workspace.repo?.match(/workspaces\/([\w-]+)\/?$/)?.[1] || '';
  const saveSpec = () => run('save', async () => {
    const r = await api(`/api/design/${id}/save-spec`, { method: 'POST' });
    return `${r.name} v${r.version}로 저장했습니다.`;
  });
  const dryRun = () => run('dry', async () => {
    if (!window.confirm(`${by.saved.name} v${by.saved.version}을 측정만 5회 실행합니다(에이전트 없음, 사용량 없음). 계속할까요?`)) return '';
    await api('/api/commands', { method: 'POST', body: { type: 'start_run', run_id: null, payload: { spec: by.saved.name, version: by.saved.version, iterations: 5, dry_run: true } } });
    return '측정 런 시작 명령을 보냈습니다.';
  });

  return (
    <div className="card readiness">
      <div className="row-between"><h2>실행 준비</h2><span className="muted small">{done}/{items.length}</span></div>
      <ol className="checklist">
        <Item it={by.git}>
          {!by.git.ok && by.git.state && (
            <details className="block wide">
              <summary className="small">git 초기화 + 첫 커밋 (.gitignore 확인)</summary>
              {by.git.heavy?.length > 0 && (
                <div className="heavy">
                  <span className="muted small">큰 폴더·파일 — 눌러서 제외 목록에 추가</span>
                  <div className="chip-row">
                    {by.git.heavy.map((h) => {
                      const cur = gitignore ?? by.git.gitignore;
                      const has = cur.split('\n').includes(h.path);
                      return (
                        <button key={h.path} type="button" className={`chip-btn ${has ? 'on' : ''}`} disabled={has}
                          onClick={() => setGitignore(`${cur.replace(/\n*$/, '\n')}${h.path}\n`)}>
                          <Icon name={has ? 'check' : 'plus'} size={11} /> <span className="mono">{h.path}</span> <span className="muted">{h.files.toLocaleString()}개 · {h.mb}MB</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
              <textarea className="mono small" rows={6} value={gitignore ?? by.git.gitignore} onChange={(e) => setGitignore(e.target.value)} />
              <p className="muted small">데이터·영상·빌드 산출물·비밀값은 꼭 넣으세요. 2만 개 또는 300MB를 넘으면 커밋하지 않고 멈춥니다.</p>
              <button className="small primary" disabled={busy === 'git'} onClick={() => run('git', async () => {
                if (!window.confirm(`원본 저장소(${session.repo})에 git init과 첫 커밋을 만듭니다. 계속할까요?`)) return '';
                await api(`/api/design/${id}/init-repo`, { method: 'POST', body: { gitignore: gitignore ?? by.git.gitignore } });
                return 'git 초기화 요청을 보냈습니다. 잠시 후 결과가 아래 요청 결과에 나옵니다.';
              })}><Icon name="code" size={13} /> git 초기화</button>
            </details>
          )}
        </Item>
        <Item it={by.valid} />
        <Item it={by.workspace}>
          {!by.workspace.ok && !by.git.ok && <span className="muted small">원본 git 초기화가 먼저 필요합니다.</span>}
          {!by.workspace.ok && by.git.ok && (
            <div className="row-gap">
              <input className="mono" value={wsName || wsGuess} onChange={(e) => setWsName(e.target.value)} placeholder="이름 (예: etf)" />
              <button className="small" disabled={!(wsName || wsGuess) || busy === 'ws'} onClick={() => run('ws', async () => {
                const r = await api(`/api/design/${id}/workspace`, { method: 'POST', body: { source: session.repo, name: wsName || wsGuess } });
                return `${r.path} 생성 요청을 보냈습니다. 원본 ${session.repo}의 clone입니다.`;
              })}>clone 만들기</button>
            </div>
          )}
        </Item>
        <Item it={sc}>
          {(sc.files.length > 0 || scripts.length > 0) && (
            <>
              {!sc.folder && (
                <label className="field"><span>저장 폴더 (phase0/evals/…)</span><input className="mono" value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="my_eval" /></label>
              )}
              <ul className="file-list">
                {(sc.files.length ? sc.files : scripts.map((s) => ({ filename: s.filename, exists: false, draft: true }))).map((f) => (
                  <li key={f.filename}>
                    <Icon name={f.exists ? 'check' : 'file'} size={13} className={f.exists ? 'good-text' : 'muted'} />
                    <span className="mono small ellipsis">{f.filename}</span>
                    {f.draft ? <button className="small" disabled={!dir || busy === `script:${f.filename}`} onClick={() => saveScript(f.filename, f.exists)}>{f.exists ? '다시 저장' : '저장'}</button>
                      : !f.exists && <span className="muted small">초안 없음</span>}
                  </li>
                ))}
              </ul>
            </>
          )}
        </Item>
        <Item it={by.saved}>
          <button className={by.saved.ok ? 'small ghost' : 'small primary'} disabled={!by.valid.ok || by.saved.ok || busy === 'save'} onClick={saveSpec}>
            <Icon name="save" size={13} /> {by.saved.version ? '새 버전으로 저장' : 'Spec 저장'}
          </button>
        </Item>
        <Item it={by.noise}>
          {by.noise.dry_run && <a className="small" href={`#/runs/${encodeURIComponent(by.noise.dry_run.id)}`}>측정 런 결과 보기 ›</a>}
          {!by.noise.ok && (
            <button className="small" disabled={!by.saved.ok || !by.workspace.ok || !sc.ok || !orchestrator?.online || busy === 'dry'} onClick={dryRun}>
              <Icon name="beaker" size={13} /> 측정만 실행
            </button>
          )}
        </Item>
      </ol>
      {done === items.length ? (
        <a className="button-link primary block-btn" href={`#/specs/${encodeURIComponent(by.saved.name)}`}><Icon name="play" size={14} /> 런 시작 화면으로</a>
      ) : <p className="muted small">위 단계가 모두 채워지면 이 Spec으로 런을 시작할 수 있습니다.</p>}
      {msg && <p className="muted small">{msg}</p>}
    </div>
  );
}

function Item({ it, children }) {
  return (
    <li className={`check-item ${it.ok ? 'ok' : ''}`}>
      <span className="check-icon"><Icon name={it.ok ? 'check' : 'circle'} size={14} /></span>
      <div className="check-body">
        <div className="row-between"><b className="small">{it.label}</b><span className={`small ${it.ok ? 'good-text' : 'muted'} ellipsis`}>{it.detail}</span></div>
        {children}
      </div>
    </li>
  );
}

// 대화 속 실행 카드 — 도우미가 제안한 일을 사용자가 눌러서 실행합니다. '모두 실행'은 위에서부터 하나씩, 끝나기를 기다리며 진행합니다.
const ACTION = {
  init_repo: ['code', '원본을 git 저장소로', (a, s) => `${s.repo} · git init + 첫 커밋`],
  create_workspace: ['folder', '작업 공간 만들기', (a) => `workspaces/${a.name}`],
  save_script: ['file', '평가 스크립트 저장', (a) => `evals/${a.folder}/${a.file}`],
  save_spec: ['save', 'Spec 저장', () => '보드의 현재 내용으로 새 버전'],
  dry_run: ['beaker', '측정만 실행', (a) => `반복 ${a.iterations || 5}회 · 사용량 없음`],
};
const FINAL = new Set(['applied', 'failed', 'rejected', 'expired']);
const actionState = (a) => {
  const st = a.command?.status;
  if (st === 'queued' || st === 'delivered') return ['running', '실행 중', 'info'];
  if (st === 'applied') return ['ok', '완료', 'good'];
  if (st && st !== 'applied') return ['fail', st === 'rejected' ? '거부됨' : '실패', 'danger'];
  if (a.done) return ['ok', '이미 됨', 'good'];
  return ['todo', '대기', 'neutral'];
};

export function ActionCards({ sid, actions, session, reload, fetchSession }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const todo = actions.filter((a) => ['todo', 'fail'].includes(actionState(a)[0]));
  const runOne = async (a) => {
    await api(`/api/design/${sid}/actions/${a.key}`, { method: 'POST' });
    // 명령이 끝날 때까지 기다립니다(오케스트레이터가 1초마다 확인, 최대 3분).
    for (let i = 0; i < 90; i++) {
      const d = await fetchSession();
      reload();   // 카드의 '실행 중/완료' 표시도 바로 갱신
      const cur = d.messages.flatMap((m) => m.actions || []).find((x) => x.key === a.key);
      const st = cur?.command?.status;
      if (st && FINAL.has(st)) { if (st !== 'applied') throw new Error(`${ACTION[a.type][1]}: ${cur.command.result || st}`); return; }
      await new Promise((r) => setTimeout(r, 2000));
    }
    throw new Error(`${ACTION[a.type][1]}: 응답 시간 초과`);
  };
  const run = async (list) => {
    if (list.some((a) => a.type === 'init_repo') && !window.confirm(`원본 저장소(${session.repo})에 git init과 첫 커밋을 만듭니다. 계속할까요?`)) return;
    if (list.length > 1 && !window.confirm(`${list.length}개 작업을 차례로 실행합니다. 실패하면 거기서 멈춥니다.`)) return;
    setBusy(true); setErr('');
    try { for (const a of list) await runOne(a); } catch (e) { setErr(e.message); }
    setBusy(false); reload();
  };
  return (
    <div className="action-cards">
      <div className="row-between"><b className="small"><Icon name="bolt" size={13} /> 실행 요청</b>
        {todo.length > 1 && <button className="small primary" disabled={busy} onClick={() => run(todo)}><Icon name="play" size={12} /> 모두 실행 ({todo.length})</button>}
      </div>
      <ol>
        {actions.map((a) => {
          const [k, label, tone] = actionState(a);
          const [icon, title, detail] = ACTION[a.type];
          return (
            <li key={a.key} className={`action a-${k}`}>
              <span className="action-icon"><Icon name={k === 'ok' ? 'check' : k === 'fail' ? 'x' : icon} size={14} /></span>
              <div className="action-main">
                <b className="small">{title}</b>
                <span className="muted small mono ellipsis">{detail(a, session)}</span>
                {a.type === 'init_repo' && a.gitignore && (
                  <details><summary className="small muted">.gitignore 추가 {String(a.gitignore).split('\n').filter(Boolean).length}줄</summary><pre className="md-code">{a.gitignore}</pre></details>
                )}
                {a.command?.result && <span className={`small clamp-2 ${k === 'fail' ? 'error' : 'muted'}`} title={a.command.result}>{a.command.result}</span>}
              </div>
              {k === 'running' ? <span className="badge info"><span className="pulse" />{label}</span>
                : k === 'ok' ? <span className={`badge ${tone}`}>{label}</span>
                : <button className="small" disabled={busy} onClick={() => run([a])}>{k === 'fail' ? '다시' : '실행'}</button>}
            </li>
          );
        })}
      </ol>
      {err && <p className="error small">{err}</p>}
    </div>
  );
}

// 도우미가 전용 도구로 직접 실행한 작업의 기록 — 답변 말풍선 안에 결과와 함께 보여줍니다.
const LOG_LABEL = {
  init_repo: ['code', '원본 git 초기화', (i, n) => `.gitignore ${n}줄`],
  create_workspace: ['folder', '작업 공간 만들기', (i) => `workspaces/${i.name}`],
  save_script: ['file', '평가 스크립트 저장', (i, n) => `evals/${i.folder}/${i.file} · ${n}줄`],
  save_spec: ['save', 'Spec 저장', (i) => (i.spec && Object.keys(i.spec).length ? `변경: ${Object.keys(i.spec).join(', ')}` : '보드 내용')],
  dry_run: ['beaker', '측정만 런 시작', (i) => `${i.spec} · ${i.iterations || 5}회`],
  run_status: ['gauge', '런 상태 확인', (i) => i.run_id || i.spec || '최근 런'],
  run_log: ['file', '런 로그 확인', (i) => `${i.run_id} · ${i.lines || 80}줄`],
  stop_run: ['stop', '런 정지', (i) => i.run_id],
};
export function ActionLog({ actions }) {
  return (
    <div className="action-cards log">
      <b className="small"><Icon name="bolt" size={13} /> 도우미가 실행한 작업</b>
      <ol>
        {actions.map((a) => {
          const [icon, title, detail] = LOG_LABEL[a.tool] || ['more', a.tool, () => ''];
          const k = a.ok === undefined ? 'running' : a.ok ? 'ok' : 'fail';
          return (
            <li key={a.id} className={`action a-${k}`}>
              <span className="action-icon"><Icon name={k === 'ok' ? 'check' : k === 'fail' ? 'x' : icon} size={14} /></span>
              <div className="action-main">
                <b className="small">{title}</b>
                <span className="muted small mono ellipsis">{detail(a.input || {}, a.lines)}</span>
                {a.result && <span className={`small clamp-2 ${k === 'fail' ? 'error' : 'muted'}`} title={a.result}>{a.result.replace(/^applied: /, '')}</span>}
              </div>
              {k === 'running' ? <span className="badge info"><span className="pulse" />실행 중</span>
                : <span className={`badge ${k === 'ok' ? 'good' : 'danger'}`}>{k === 'ok' ? '완료' : '실패'}</span>}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
