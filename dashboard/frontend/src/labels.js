// 화면 표시용 라벨과 색 — 상태값 문자열은 루프 이벤트(phase0/loop.py)와 백엔드 요약 로직의 값과 같아야 합니다.
export const RUN_STATUS = {
  running: { label: '실행 중', tone: 'info' },
  finished: { label: '완료', tone: 'neutral' },
  paused: { label: '일시정지', tone: 'warn' },
  stale: { label: '응답 없음', tone: 'danger' },
  aborted: { label: '중단됨', tone: 'warn' },
};

export const PHASE = {
  agent: '에이전트 작업 중',
  building: '빌드 중',
  benchmarking: '측정 중',
  judging: '판정 중',
  done: '완료',
  interrupted: '중단됨',
  approval: '승인 대기',
  planner: '분석 중 (가설 후보 작성)',
  reviewer: '검토 중 (채택 후보 검사)',
  baseline: '기준 측정 중 (현재 최고 버전 성능 측정, 3~4분)',
};

export const DECISION = {
  accepted: { label: '채택', tone: 'good' },
  rejected_no_gain: { label: '개선 부족', tone: 'neutral' },
  rejected_snapshot: { label: '결과 불일치', tone: 'danger' },
  rejected_build: { label: '빌드 실패', tone: 'danger' },
  rejected_scope: { label: '범위 위반', tone: 'danger' },
  rejected_errors: { label: '페이지 오류', tone: 'danger' },
  rejected_bench_error: { label: '측정 오류', tone: 'danger' },
  no_change: { label: '변경 없음', tone: 'neutral' },
  agent_timeout: { label: '시간 초과', tone: 'warn' },
  measured: { label: '측정만', tone: 'neutral' },
  interrupted: { label: '중단됨', tone: 'warn' },
  rejected_by_human: { label: '사람이 거절', tone: 'warn' },
  rejected_gate: { label: '검사 실패', tone: 'danger' },
  rejected_early: { label: '조기 탈락', tone: 'warn' },
};

export const GATE = {
  build: '빌드',
  snapshot: '결과 일치',
  no_page_errors: '페이지 오류 없음',
  bench: '측정',
  scope: '변경 범위',
  eval: '평가 실행',
  early_stop: '학습 곡선',
};

// 단계 평가의 gate 이름(예: 'proxy:early_stop', 'promote:proxy')을 읽기 쉬운 라벨로
export const gateLabel = (g) => {
  if (g.startsWith('promote:')) return `${g.slice(8)} 승급`;
  const [stage, name] = g.includes(':') ? g.split(/:(.*)/s) : [null, g];
  return stage ? `${stage} · ${GATE[name] || name}` : GATE[g] || g;
};

export const EVENT_LABEL = {
  run_start: '런 시작',
  run_end: '런 종료',
  iteration_start: '반복 시작',
  agent_tool: '에이전트 도구',
  agent_done: '에이전트 완료',
  candidate_commit: '후보 커밋',
  gate: '검사',
  metric: '측정 결과',
  judgment: '판정',
  usage: '사용량',
  paused: '일시정지',
  resumed: '재개',
  instruction_applied: '추가 지시 반영',
  run_aborted: '런 중단',
  baseline_measured: '기준 측정',
  approval_required: '승인 요청',
  approval: '승인 결과',
  role_start: '역할 시작',
  role_done: '역할 완료',
  hypotheses: '가설 후보',
  hypothesis_assigned: '가설 배정',
  review: '검토 결과',
  roles_changed: '할당 변경',
  role_usage: '역할별 사용량',
  stop_recommended: '조기 종료 권고',
  model_fallback: '모델 대체',
};

export const fmtMs = (v) => (v == null ? '—' : v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${Math.round(v)}ms`);
export const fmtPct = (v) => (v == null ? '—' : `${Math.round(v * 100)}%`);
export const fmtTime = (iso) => (iso ? iso.replace('T', ' ').slice(5, 16) : '—');
export const fmtDuration = (ms) => {
  if (ms == null) return '—';
  const s = Math.round(ms / 1000);
  return s >= 60 ? `${Math.floor(s / 60)}분 ${s % 60}초` : `${s}초`;
};

// 대시보드 명령 — when은 "언제 반영되는지"를 사용자에게 알려주는 문구
export const COMMAND = {
  start_run: { label: '런 시작', when: '즉시' },
  stop: { label: '즉시 중단', when: '즉시 (진행 중인 반복은 버려짐)' },
  stop_after: { label: '이번 반복 후 종료', when: '현재 반복이 끝난 뒤' },
  pause: { label: '일시정지', when: '다음 체크포인트 (반복 시작 전 또는 에이전트 완료 후)' },
  resume: { label: '재개', when: '즉시' },
  inject: { label: '추가 지시', when: '다음 반복 시작 시 에이전트 프롬프트에 포함' },
  approve: { label: '승인', when: '즉시' },
  reject: { label: '거절', when: '즉시' },
  set_roles: { label: '역할 변경', when: '다음 반복 시작 시' },
};
export const COMMAND_STATUS = {
  queued: { label: '대기', tone: 'neutral' },
  delivered: { label: '전달됨', tone: 'info' },
  applied: { label: '반영됨', tone: 'good' },
  rejected: { label: '거부됨', tone: 'danger' },
  failed: { label: '실패', tone: 'danger' },
  expired: { label: '만료됨', tone: 'neutral' },
};

// 지표 값 표시 — 런/Spec의 metric(name, unit, direction)을 따릅니다. ms는 1초 이상이면 초로 바꿔 보여줍니다.
export const fmtValue = (v, metric) => {
  if (v == null) return '—';
  const unit = metric?.unit ?? '';
  if (unit === 'ms') return fmtMs(v);
  const n = Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 1000) / 1000;
  return `${n}${unit}`;
};
// improvement는 항상 "양수 = 좋아짐"(루프가 방향을 반영해 계산)
export const fmtGain = (g, metric) => (g == null ? '' : g > 0 ? `개선 ${fmtValue(g, metric)}` : g < 0 ? `악화 ${fmtValue(-g, metric)}` : '변화 없음');
export const gainPct = (base, best, metric) => {
  if (!base || best == null) return null;
  const pct = metric?.direction === 'maximize' ? (best / base - 1) * 100 : (1 - best / base) * 100;
  return Math.round(pct);
};
