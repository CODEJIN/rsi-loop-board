import React, { useEffect, useState } from 'react';
import yaml from 'js-yaml';
import { api } from '../api.js';
import RolesEditor, { normalizeRoles } from '../components/RolesEditor.jsx';

const TEMPLATE = `spec: my-new-spec
version: 1
title: 새 개선 대상
project: MyProject

workspace:
  repo: /home/heejo/RSI_Loop/workspaces/my-project   # 원본의 독립 clone (루프 전용 작업 공간)
  best_branch: rsi/best
  base_ref: main

target:
  type: code.performance
  goal: 무엇을 어떻게 좋게 만들지 한 문장으로
  editable_paths:
    - src/**

agent:
  instructions: |
    - 지켜야 할 불변 조건(결과·API·UI 등)
  context: |
    - 배경, 사전 분석, 이미 시도한 것
  focus: 가장 효과가 클 것으로 보이는 변경 하나를 적용하라.
  tools: Read,Edit,Write,Glob,Grep
  timeout_min: 20

evaluation:
  command: python3 evals/my_eval/eval.py   # --repo/--out/--label 인자를 받아 JSON을 쓰는 명령
  timeout_min: 30
  description: |
    측정 방법과 gate(통과해야 하는 검사)를 에이전트가 이해할 수 있게 설명

metric:
  name: 지표 이름
  unit: ms
  direction: minimize
  noise_threshold:          # 비워두고 측정만 런으로 잡음을 잰 뒤 정하는 것을 권장

judge:
  policy: human             # 새 대상은 사람 판정으로 시작해 신뢰가 쌓이면 hybrid/auto로
  hybrid:
    improvement_pct_gte: 30
    diff_lines_gte: 200

budget:
  max_iterations: 5
  stop_if_five_hour_utilization_gte: 0.80
  stop_if_seven_day_utilization_gte: 0.70
`;

const TARGET_TYPES = [
  ['code.performance', '코드 성능'], ['code.correctness', '코드 정확도'], ['code.quality', '코드 품질'],
  ['docs', '문서·콘텐츠'], ['agent.process', '에이전트 프로세스 (지침·스킬)'], ['config', '데이터·설정'], ['other', '기타'],
];

// 중첩 객체의 "a.b.c" 경로 읽기/쓰기
const getAt = (o, path) => path.split('.').reduce((v, k) => (v == null ? v : v[k]), o);
const setAt = (o, path, value) => {
  const keys = path.split('.');
  const next = structuredClone(o);
  let cur = next;
  keys.slice(0, -1).forEach((k) => { if (typeof cur[k] !== 'object' || cur[k] == null) cur[k] = {}; cur = cur[k]; });
  cur[keys.at(-1)] = value;
  return next;
};

function Field({ label, hint, children }) {
  return <label className="field"><span>{label}{hint && <em className="muted"> — {hint}</em>}</span>{children}</label>;
}

export default function SpecEditor({ name, fromVersion }) {
  const isNew = !name;
  const [text, setText] = useState(null);
  const [obj, setObj] = useState(null);
  const [mode, setMode] = useState('form');
  const [note, setNote] = useState('');
  const [errors, setErrors] = useState([]);
  const [busy, setBusy] = useState(false);
  const [parseError, setParseError] = useState('');

  useEffect(() => {
    const init = (t) => { setText(t); try { setObj(yaml.load(t)); } catch (e) { setParseError(String(e)); setMode('yaml'); } };
    if (isNew) {
      // 설계 대화에서 "편집기로 열기"로 넘어온 초안이 있으면 그것으로 시작합니다.
      let draft = null;
      try { draft = sessionStorage.getItem('rsi-spec-draft'); sessionStorage.removeItem('rsi-spec-draft'); } catch { /* 없음 */ }
      init(draft || TEMPLATE);
    }
    else api(`/api/specs/${encodeURIComponent(name)}/${fromVersion}`).then((v) => init(v.yaml)).catch((e) => setErrors([e.message]));
  }, [name, fromVersion, isNew]);

  if (text === null) return <p className="muted">{errors[0] || '불러오는 중…'}</p>;

  // 폼 ↔ YAML 전환: 폼에서 고친 내용은 YAML로 다시 쓰여서 주석이 사라집니다(안내 문구로 알림).
  const switchMode = (m) => {
    if (m === mode) return;
    if (m === 'yaml' && obj) setText(yaml.dump(obj, { lineWidth: 120, noRefs: true }));
    if (m === 'form') {
      try { setObj(yaml.load(text)); setParseError(''); } catch (e) { setParseError(String(e)); return; }
    }
    setMode(m);
  };
  const currentYaml = () => (mode === 'form' ? yaml.dump(obj, { lineWidth: 120, noRefs: true }) : text);
  const bind = (path, { type = 'text', lines = false } = {}) => ({
    value: lines ? (getAt(obj, path) || []).join('\n') : (getAt(obj, path) ?? ''),
    onChange: (e) => {
      let v = e.target.value;
      if (lines) v = v.split('\n').map((s) => s.trim()).filter(Boolean);
      else if (type === 'number') v = v === '' ? null : Number(v);
      setObj(setAt(obj, path, v));
    },
  });

  const check = async () => {
    const r = await api('/api/specs/validate', { method: 'POST', body: { yaml: currentYaml() } });
    setErrors(r.ok ? [] : r.errors);
    return r.ok;
  };
  const save = async () => {
    setBusy(true);
    try {
      if (await check()) {
        const r = await api('/api/specs', { method: 'POST', body: { yaml: currentYaml(), note: note || null } });
        window.location.hash = `#/specs/${encodeURIComponent(r.name)}/v/${r.version}`;
      }
    } catch (e) { setErrors(e.errors || [e.message || '저장 실패']); }
    setBusy(false);
  };

  return (
    <div className="stack narrow-page">
      <a href={isNew ? '#/specs' : `#/specs/${encodeURIComponent(name)}/v/${fromVersion}`} className="muted small">← 돌아가기</a>
      <div className="row-between wrap">
        <h1 className="run-title">{isNew ? '새 Spec' : `${name} — v${fromVersion}에서 새 버전`}</h1>
        <div className="tabs">
          <button className={mode === 'form' ? 'active' : ''} onClick={() => switchMode('form')}>폼</button>
          <button className={mode === 'yaml' ? 'active' : ''} onClick={() => switchMode('yaml')}>YAML</button>
        </div>
      </div>
      {mode === 'form' && <p className="muted small">폼으로 저장하면 YAML의 주석은 사라집니다. 주석을 지키려면 YAML 탭에서 편집하세요.</p>}
      {parseError && <p className="error small">YAML 오류: {parseError}</p>}

      {mode === 'yaml' ? (
        <textarea className="yaml-editor mono" spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} />
      ) : obj && (
        <>
          <section className="card">
            <h2>기본</h2>
            <div className="form-grid">
              <Field label="이름 (spec)" hint="소문자·숫자·하이픈"><input {...bind('spec')} disabled={!isNew} /></Field>
              <Field label="제목"><input {...bind('title')} /></Field>
              <Field label="프로젝트"><input {...bind('project')} /></Field>
            </div>
          </section>
          <section className="card">
            <h2>개선 대상</h2>
            <Field label="유형"><select {...bind('target.type')}>{TARGET_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
            <Field label="목표" hint="에이전트가 받는 한 줄 목표"><input {...bind('target.goal')} /></Field>
            <Field label="수정 가능 경로" hint="한 줄에 하나, glob"><textarea rows={3} className="mono" {...bind('target.editable_paths', { lines: true })} /></Field>
            <div className="form-grid">
              <Field label="작업 저장소"><input className="mono" {...bind('workspace.repo')} /></Field>
              <Field label="best 브랜치"><input className="mono" {...bind('workspace.best_branch')} /></Field>
              <Field label="기준 브랜치"><input className="mono" {...bind('workspace.base_ref')} /></Field>
            </div>
          </section>
          <section className="card">
            <h2>평가 · 지표</h2>
            <Field label="평가 명령" hint="phase0/ 기준, --repo/--out/--label이 붙어 실행"><input className="mono" {...bind('evaluation.command')} /></Field>
            <Field label="정리 명령 (선택)"><input className="mono" {...bind('evaluation.cleanup_command')} /></Field>
            <Field label="측정 방법 설명" hint="에이전트에게 그대로 전달"><textarea rows={4} {...bind('evaluation.description')} /></Field>
            <div className="form-grid">
              <Field label="지표 이름"><input {...bind('metric.name')} /></Field>
              <Field label="단위"><input {...bind('metric.unit')} /></Field>
              <Field label="방향"><select {...bind('metric.direction')}><option value="minimize">낮을수록 좋음</option><option value="maximize">높을수록 좋음</option></select></Field>
              <Field label="채택 기준 (잡음)" hint="비우면 측정만 가능"><input inputMode="decimal" {...bind('metric.noise_threshold', { type: 'number' })} /></Field>
            </div>
          </section>
          <section className="card">
            <h2>에이전트</h2>
            <Field label="지시 (불변 조건·규칙)"><textarea rows={5} {...bind('agent.instructions')} /></Field>
            <Field label="배경 · 사전 분석"><textarea rows={5} {...bind('agent.context')} /></Field>
            <Field label="마무리 지시 (focus)"><input {...bind('agent.focus')} /></Field>
            <div className="form-grid">
              <Field label="도구"><input className="mono" {...bind('agent.tools')} /></Field>
              <Field label="시간 제한(분)"><input inputMode="numeric" {...bind('agent.timeout_min', { type: 'number' })} /></Field>
              <Field label="모델 (선택)"><input className="mono" {...bind('agent.model')} placeholder="기본값" /></Field>
            </div>
          </section>
          <section className="card">
            <h2>역할 · 할당</h2>
            {obj.roles ? (
              <>
                <RolesEditor roles={obj.roles} onChange={(roles) => setObj({ ...obj, roles })}
                  allocation={obj.allocation || { mode: 'manual', run_budget_pct: null, plateau_after: 2 }}
                  onAllocationChange={(allocation) => setObj({ ...obj, allocation })} />
                <button className="ghost small" onClick={() => { const { roles, allocation, ...rest } = obj; setObj(rest); }}>역할 분리 끄기 (구현만, 기존 동작)</button>
              </>
            ) : (
              <>
                <p className="muted small">지금은 구현 역할만 돕니다(기존 동작). 역할 분리를 켜면 분석(가설 후보)·검토(채택 후보 검사)를 쓸 수 있습니다.</p>
                <button onClick={() => setObj({ ...obj, roles: normalizeRoles({}), allocation: { mode: 'manual', run_budget_pct: 20, plateau_after: 2 } })}>역할 분리 켜기</button>
              </>
            )}
          </section>
          <section className="card">
            <h2>판정 · 예산</h2>
            <div className="form-grid">
              <Field label="판정 정책">
                <select {...bind('judge.policy')}><option value="auto">자동</option><option value="human">사람</option><option value="hybrid">혼합</option></select>
              </Field>
              <Field label="혼합: 개선폭 ≥ (%)"><input inputMode="numeric" {...bind('judge.hybrid.improvement_pct_gte', { type: 'number' })} /></Field>
              <Field label="혼합: 변경 줄 ≥"><input inputMode="numeric" {...bind('judge.hybrid.diff_lines_gte', { type: 'number' })} /></Field>
              <Field label="최대 반복"><input inputMode="numeric" {...bind('budget.max_iterations', { type: 'number' })} /></Field>
              <Field label="5시간 사용량 대기 기준"><input inputMode="decimal" {...bind('budget.stop_if_five_hour_utilization_gte', { type: 'number' })} /></Field>
              <Field label="7일 사용량 대기 기준"><input inputMode="decimal" {...bind('budget.stop_if_seven_day_utilization_gte', { type: 'number' })} /></Field>
            </div>
          </section>
        </>
      )}

      <section className="card">
        <Field label="버전 메모" hint="무엇을 왜 바꿨는지"><input value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        {errors.length > 0 && <ul className="error small">{errors.map((e, i) => <li key={i}>{e}</li>)}</ul>}
        <div className="row-gap">
          <button disabled={busy} onClick={check}>검사</button>
          <button className="primary" disabled={busy} onClick={save}>저장 (새 버전)</button>
        </div>
      </section>
    </div>
  );
}
