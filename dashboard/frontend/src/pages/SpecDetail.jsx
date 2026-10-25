import React, { useEffect, useState } from 'react';
import { api } from '../api.js';
import { useApi } from '../hooks.js';
import { StatusBadge } from '../components/common.jsx';
import Icon from '../components/icons.jsx';
import { StartRun } from '../components/Controls.jsx';
import { fmtTime, fmtValue, gainPct } from '../labels.js';
import { lineDiff } from '../specText.js';

const JUDGE = {
  auto: ['bolt', '자동', 'gate 통과 + 기준 이상 개선이면 채택'],
  human: ['user', '사람', '채택 후보마다 승인'],
  hybrid: ['gavel', '혼합', '조건에 걸리면 사람 승인'],
};
const ROLE = { planner: ['bulb', '분석'], implementer: ['code', '구현'], reviewer: ['shield', '검토'] };
const TIER = { high: '상위', standard: '표준', light: '경량' };

// 숫자 하나가 핵심인 요약 타일
function Tile({ icon, label, value, sub, tone }) {
  return (
    <div className={`sum-tile ${tone ? `t-${tone}` : ''}`}>
      <div className="stat-label"><Icon name={icon} size={13} /> {label}</div>
      <div className="sum-value">{value}</div>
      {sub && <div className="muted small">{sub}</div>}
    </div>
  );
}

// 구성 카드 — 제목 + 칩/짧은 값
function Part({ icon, title, children }) {
  return (
    <div className="part">
      <div className="part-head"><Icon name={icon} size={14} /><b className="small">{title}</b></div>
      {children}
    </div>
  );
}

// 긴 글은 두 줄만 보이고 눌러서 펼칩니다.
function LongText({ icon, title, text }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  const lines = text.trim().split('\n').length;
  return (
    <button type="button" className={`longtext ${open ? 'open' : ''}`} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
      <span className="part-head"><Icon name={icon} size={14} /><b className="small">{title}</b><span className="muted small">{lines}줄</span>
        <Icon name="chevron" size={14} className="longtext-chev" /></span>
      <span className={`pre-text small ${open ? '' : 'clamp-2'}`}>{text.trim()}</span>
    </button>
  );
}

function Summary({ d, runs }) {
  const m = d.metric || {}, j = JUDGE[d.judge?.policy || 'auto'], b = d.budget || {}, e = d.evaluation || {};
  // 이 Spec의 누적 성과: 가장 이른 개선 런의 기준값 → 지금까지의 최고값
  const real = runs.filter((r) => !r.dry_run && r.best_objective != null);
  const first = [...real].sort((a, b2) => a.started_at.localeCompare(b2.started_at))[0];
  const bestVal = real.length ? (m.direction === 'maximize' ? Math.max : Math.min)(...real.map((r) => r.best_objective)) : null;
  const pct = first ? gainPct(first.baseline_objective, bestVal, m) : null;
  const roles = d.roles ? Object.entries(d.roles).filter(([, r]) => r?.enabled !== false) : [['implementer', {}]];
  return (
    <div className="spec-summary">
      <p className="sum-goal">{d.target?.goal}</p>
      <div className="sum-tiles">
        <Tile icon="trend" label="지표" value={<>{m.name}</>} sub={`${m.direction === 'minimize' ? '↓ 낮을수록' : '↑ 높을수록'} 좋음${m.unit ? ` · ${m.unit}` : ''}`} />
        <Tile icon="target" label="채택 기준" tone={m.noise_threshold == null ? 'warn' : null}
          value={m.noise_threshold == null ? '미정' : `> ${fmtValue(m.noise_threshold, m)}`} sub={m.noise_threshold == null ? '측정만 런으로 잡음 측정' : '이보다 크게 개선되면 채택'} />
        <Tile icon={j[0]} label="판정" value={j[1]} sub={d.judge?.policy === 'hybrid' ? `${d.judge?.hybrid?.improvement_pct_gte ?? '—'}%↑ 또는 ${d.judge?.hybrid?.diff_lines_gte ?? '—'}줄↑이면 승인` : j[2]} />
        <Tile icon="check" label="누적 성과" tone={pct > 0 ? 'good' : null} value={pct == null ? '—' : `${pct > 0 ? '−' : '+'}${Math.abs(pct)}%`}
          sub={pct == null ? '아직 개선 런 없음' : `${fmtValue(first.baseline_objective, m)} → ${fmtValue(bestVal, m)}`} />
      </div>

      <div className="parts">
        <Part icon="folder" title="수정 범위">
          <div className="chip-row">{(d.target?.editable_paths || []).map((p) => <span key={p} className="chip-static mono">{p}</span>)}</div>
        </Part>
        <Part icon="beaker" title="평가">
          <code className="board-cmd">{e.command}</code>
          <div className="chip-row">
            {e.timeout_min && <span className="chip-static"><Icon name="clock" size={11} /> {e.timeout_min}분</span>}
            {e.node && <span className="chip-static"><Icon name="plug" size={11} /> {e.node}</span>}
            {(e.matrix || []).map((x, i) => <span key={i} className="chip-static"><Icon name="plug" size={11} /> {x.name || x.node}</span>)}
            {e.cleanup_command && <span className="chip-static tool">정리 명령 있음</span>}
          </div>
          {e.stages?.length > 0 && (
            <div className="stage-flow small">
              {e.stages.map((st, i) => (
                <React.Fragment key={st.name}>
                  {i > 0 && <Icon name="chevron" size={12} className="muted" />}
                  <span className="chip-static" title={st.args || ''}>{st.name}<span className="muted"> {st.timeout_min ?? e.timeout_min ?? 30}분
                    {st.promote_within_pct != null ? ` · ${st.promote_within_pct}%↓ 탈락` : ''}{(st.early_stop || e.early_stop) ? ' · 곡선 감시' : ''}</span></span>
                </React.Fragment>
              ))}
            </div>
          )}
        </Part>
        <Part icon="code" title="작업 공간">
          <code className="board-cmd">{d.workspace?.repo}</code>
          <div className="chip-row">
            <span className="chip-static mono">{d.workspace?.base_ref || 'main'}</span>
            <span className="muted small">→</span>
            <span className="chip-static mono">{d.workspace?.best_branch || 'rsi/best'}</span>
            {d.target?.type && <span className="chip-static tool">{d.target.type}</span>}
          </div>
        </Part>
        <Part icon="gauge" title="역할 · 예산">
          <div className="chip-row">
            {roles.map(([k, r]) => <span key={k} className="chip-static"><Icon name={ROLE[k]?.[0] || 'more'} size={11} /> {ROLE[k]?.[1] || k}{r.tier && <span className="muted"> · {TIER[r.tier] || r.tier}</span>}</span>)}
          </div>
          <div className="muted small">반복 {b.max_iterations ?? 5}회 · 5시간 {Math.round((b.stop_if_five_hour_utilization_gte ?? 0.8) * 100)}% / 7일 {Math.round((b.stop_if_seven_day_utilization_gte ?? 0.7) * 100)}%에서 대기
            {d.allocation?.run_budget_pct ? ` · 런 예산 ${d.allocation.run_budget_pct}%p` : ''}</div>
        </Part>
      </div>

      <div className="longtexts">
        <LongText icon="file" title="에이전트 지시" text={d.agent?.instructions} />
        <LongText icon="bulb" title="배경 · 사전 분석" text={d.agent?.context} />
        <LongText icon="beaker" title="측정 방법 · gate" text={e.description} />
      </div>
    </div>
  );
}

export default function SpecDetail({ name, version, lastEvent }) {
  const { data: list, error } = useApi(`/api/specs/${encodeURIComponent(name)}`, { refreshKey: lastEvent, debounceMs: 1500 });
  const { data: overview } = useApi('/api/overview', { pollMs: 15000 });
  const [spec, setSpec] = useState(null);
  const [prev, setPrev] = useState(null);
  const [view, setView] = useState('summary');
  const [allRuns, setAllRuns] = useState(false);
  const current = version || list?.versions?.[0]?.version;

  useEffect(() => {
    if (!current) return;
    setSpec(null); setPrev(null);
    api(`/api/specs/${encodeURIComponent(name)}/${current}`).then(setSpec).catch(() => {});
    // 바로 이전 버전(번호가 비어 있을 수 있어 목록에서 찾음)과 비교합니다.
    const prevVersion = list?.versions?.map((v) => v.version).filter((v) => v < current).sort((a, b) => b - a)[0];
    if (prevVersion) api(`/api/specs/${encodeURIComponent(name)}/${prevVersion}`).then(setPrev).catch(() => setPrev(null));
  }, [name, current, list]);

  if (error) return <p className="error">{error.message}</p>;
  if (!list || !spec) return <p className="muted">불러오는 중…</p>;
  const d = spec.data;
  const m = d.metric || {};

  return (
    <div className="stack">
      <a href="#/specs" className="muted small">← Spec 목록</a>
      <section className="card spec-head">
        <div className="row-between wrap">
          <div className="min0">
            <h1 className="run-title">{d.title || d.spec} <span className="badge neutral">v{spec.version}</span></h1>
            <div className="muted small mono ellipsis">{d.spec} · {fmtTime(spec.created_at)} · {spec.source}</div>
            {spec.note && <p className="small muted">{spec.note}</p>}
          </div>
          <a className="button-link" href={`#/specs/${encodeURIComponent(name)}/edit/${spec.version}`}><Icon name="code" size={13} /> 이 버전에서 새 버전</a>
        </div>
      </section>

      <section className="grid-main">
        <div className="card">
          <div className="tabs">
            {[['summary', '요약'], ['yaml', 'YAML'], ['diff', prev ? `v${prev.version} 대비 변경` : '이전 버전 없음']].map(([k, l]) => (
              <button key={k} className={view === k ? 'active' : ''} disabled={k === 'diff' && !prev} onClick={() => setView(k)}>{l}</button>
            ))}
          </div>
          {view === 'summary' && <Summary d={d} runs={list.runs} />}
          {view === 'yaml' && <pre className="diff">{spec.yaml}</pre>}
          {view === 'diff' && prev && (
            <pre className="diff">
              {lineDiff(prev.yaml, spec.yaml).map((l, i) => (
                <span key={i} className={l.type === 'add' ? 'add' : l.type === 'del' ? 'del' : 'muted'}>{l.type === 'add' ? '+ ' : l.type === 'del' ? '- ' : '  '}{l.text}{'\n'}</span>
              ))}
            </pre>
          )}
        </div>

        <div className="stack">
          <div className="card">
            <h2>이 버전으로 실행</h2>
            <StartRun orchestrator={overview?.orchestrator} usage={overview?.usage}
              fixedSpec={{ name: spec.name, version: spec.version, metric: m, max_iterations: d.budget?.max_iterations, title: d.title }} />
          </div>
          <div className="card">
            <h2>버전</h2>
            <div className="vcards">
              {list.versions.map((v) => {
                const n = list.runs.filter((r) => r.spec_version === v.version).length;
                const sel = v.version === spec.version;
                return (
                  <a key={v.version} href={`#/specs/${encodeURIComponent(name)}/v/${v.version}`} className={`vcard ${sel ? 'sel' : ''}`} aria-current={sel ? 'page' : undefined}>
                    <span className="vnum">v{v.version}</span>
                    <span className="vmain">
                      <span className="small">{fmtTime(v.created_at)}{v.version === list.versions[0].version && <span className="badge info vlatest">최신</span>}</span>
                      {v.note ? <span className="muted small clamp-2">{v.note}</span> : <span className="muted small">{v.source}</span>}
                    </span>
                    <span className="vruns muted small">{n ? `런 ${n}` : ''}</span>
                  </a>
                );
              })}
            </div>
          </div>
          <div className="card">
            <h2>런</h2>
            {list.runs.length === 0 ? <p className="muted small">아직 없음</p> : (
              <div className="vcards">
                {(allRuns ? list.runs : list.runs.slice(0, 5)).map((r) => {
                  const pct = gainPct(r.baseline_objective, r.best_objective, d.metric);
                  return (
                    <a key={r.id} href={`#/runs/${encodeURIComponent(r.id)}`} className="vcard">
                      <span className="vmain">
                        <span className="small">{fmtTime(r.started_at)} <span className="muted">· v{r.spec_version}{r.dry_run ? ' · 측정만' : ''}</span></span>
                        <span className="muted small">{r.best_objective != null ? `${fmtValue(r.baseline_objective, d.metric)} → ${fmtValue(r.best_objective, d.metric)}${pct ? ` (${pct > 0 ? '−' : '+'}${Math.abs(pct)}%)` : ''}` : r.baseline_objective != null ? `기준 ${fmtValue(r.baseline_objective, d.metric)}` : '측정값 없음'}</span>
                      </span>
                      <StatusBadge status={r.status} />
                    </a>
                  );
                })}
                {list.runs.length > 5 && <button className="ghost small" onClick={() => setAllRuns((v) => !v)}>{allRuns ? '접기' : `전체 ${list.runs.length}개 보기`}</button>}
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
