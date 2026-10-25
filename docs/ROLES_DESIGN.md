# RSI Loop 역할 분리 · 사용량 할당 설계

> 작성: 2026-10-25 · 상태: **채택 · 1차 구현 완료** (리뷰 반영 v2) — 범위는 §8, 검증 결과는 §11
> 관련: [PLAN_REVIEW.md](../PLAN_REVIEW.md), `phase0/loop.py`, `phase0/orchestrator.py`, `dashboard/`

## 0. 리뷰 반영 내역 (2026-10-25)

독립 리뷰 에이전트의 판정은 **조건부 채택**이었다. 아래 내용을 반영했다.

| 지적 | 반영 |
|---|---|
| reviewer를 평가 전에 매 반복 돌리면, 사용량을 들여 시간만 아끼는 꼴이다 (평가는 사용량을 쓰지 않음) | reviewer를 **평가 뒤, 채택 후보에만** 돌리는 지표 조작 검사로 옮겼다. 거부하면 버리지 않고 사람 승인으로 넘긴다 → `force_eval` 기능 삭제 |
| 가중 토큰 공식에서 cache_read가 빠졌고, 모델 계층 가중치가 없다 | 토큰 4항목 + `tier_factor`로 수정. 원시 usage는 전부 기록 |
| 역할 단위 Δutil은 1% 해상도에서 대부분 0이고, 사용자 본인 사용도 섞인다 | 역할별 사용량은 **런 단위 Δ를 가중 토큰 비중으로 나눈 추정치**로만 쓴다(사용자 사용 포함이라고 명시) |
| `run_budget_pct`는 창 리셋에서 음수가 되고, 한 반복만큼 초과하며, 기존 stop 규칙과의 관계가 없다 | 양수 증가분만 누적하고, `resetsAt`이 바뀌면 리셋으로 처리. 기준은 "남은 예산 < 최근 반복 평균 비용". 기존 `stop_if_*` 대기가 우선 |
| 모델 매핑을 대시보드 전역 설정에 두면 루프가 읽을 수 없다 | 오케스트레이터가 `start_run` 때 `model_tiers`와 역할 덮어쓰기를 **런 폴더의 spec.yaml 스냅샷에 합쳐** 넘긴다 |
| "v3로 켠다"는 틀렸다 (version은 Spec 개정 번호) | `roles` 키가 있는지로 판단 |
| 역할 사이의 체크포인트, 하위 프로세스 정리, 명령 적용 추적이 빠졌다 | 역할마다 끝난 뒤 checkpoint·stop_after 확인, `run_role`이 `_agent_proc` 관리, `roles_changed`에 `command_id` 기록 |
| 반복의 원인은 "기록 없음"이 아니라 런을 넘어 기록이 이어지지 않는 것이다 | **규칙 기반 Spec 단위 시도 기록**(LLM 없음)을 1차의 핵심으로 둠 |
| planner 기본값 high(Opus)는 Pro에서 부담 | 기본 계층을 standard로 |
| 그 밖에 | 채택 후 남은 가설은 낡음 처리하고 재계획. auto 규칙에 히스테리시스. 역할별 예상 비중 미리보기. A/B 탐색 필요성 명시 |

---

## 1. 배경과 목표

현재 반복 1회의 흐름은 **구현 에이전트 1개 → 평가 스크립트 → 판정**이다. 첫 실전 런에서 다음 약점이 드러났다.
- 2·3회차가 비슷한 아이디어로 잡음 수준의 결과를 냈다. 기록이 **런을 넘어 이어지지 않고**, 측정 세부(어느 조작이 느린지)가 시도 결과와 연결되지 않았기 때문이다.
- 분석과 구현이 한 세션에 섞여 있어, 처음 떠오른 가설로 바로 구현한다.
- 지표만 좋아지는 변경을 걸러내는 장치가 gate뿐이다.

**목표**
1. 목적별 역할을 분리한다.
2. 역할별 사용량 할당을 **사용자가 토글**할 수 있게 한다.
3. 할당은 상황에 따라 바뀌고, 그 조정의 자동화도 RSI 영역에서 다룬다.
4. 역할마다 모델 계층을 다르게 쓴다.

**비목표:** best-of-N 병렬 구현, Claude Code 내부 하위 에이전트(Task) 사용. 둘 다 사용량과 관측성 문제로 제외한다(§9).

---

## 2. 역할

모든 역할은 루프가 `claude -p`로 **각각 독립된 세션**으로 실행한다. 결과는 구조화된 텍스트로 다음 역할에 넘긴다.

| 역할 | 목적 | 도구 | 출력 | 기본 실행 시점 | 1차 구현 |
|---|---|---|---|---|---|
| **planner** | 가설 후보를 순위와 근거와 함께 제안 | Read, Glob, Grep | ```json 블록: `[{id, title, rationale, expected_gain, risk, files, overlaps}]` (최대 5개) | 런 시작, 정체, 채택 직후(가설이 낡아지므로) | ✅ |
| **implementer** | 배정된 가설 하나를 구현 | Read, Edit, Write, Glob, Grep | `SUMMARY:` / `HYPOTHESIS:` | 매 반복 (끌 수 없음) | ✅ (기존) |
| **reviewer** | **채택 후보**의 diff가 목표에 맞는 진짜 개선인지, 지표 조작·불변 조건 위반은 없는지 검사 | Read, Glob, Grep | `VERDICT: pass\|reject`, `RISK: low\|medium\|high`, `REASONS:` 목록 | 개선폭이 채택 기준을 넘은 후보에만 (`on_accept_candidate`) | ✅ |
| **judge** | 수치만으로 판단하기 어려운 대상의 채택 판단 | Read, Glob, Grep | `DECISION: accept\|reject\|ask_human` | 판정 정책이 부를 때 | ⏸ 보류 |
| **retro (LLM)** | 교훈 문장 생성 | 없음 | `LESSON:` | — | ⏸ 보류 (규칙 기반 기록으로 대체) |

### 2.1 세부 규칙
- **planner**
  - 가설마다 시도 기록과 대조해 `overlaps`(겹치는 과거 시도)를 적는다.
  - 루프는 이번 런에서 아직 쓰지 않은 가설 중 순위가 가장 높은 것을 implementer에게 배정한다.
  - 후보를 다 쓰면 정체로 보고 재계획한다(`when`이 허용할 때).
  - 채택이 일어나면 남은 가설을 `stale`로 표시하고, 다음 반복 전에 재계획한다.
  - 파싱에 실패하면 가설 없이 implementer 단독으로 진행하고 `role_done.ok=false`를 남긴다.
- **reviewer**
  - `reject`이거나 `RISK: high`이면, 판정 정책과 관계없이 **사람 승인 대기**로 넘긴다. 사유에는 검토 결과를 붙인다.
  - `pass`이면 기존 판정 정책을 따른다(`auto`면 채택, `human`이면 승인 대기, `hybrid`면 조건 확인).
  - 파싱에 실패하면 안전하게 사람 승인으로 넘긴다.
  - reviewer를 끄면 기존 동작과 같다.
- **시도 기록 (규칙 기반, LLM 없음)**
  - 위치: `phase0/state/attempts/<spec>.jsonl` (Spec 단위로 런을 넘어 누적)
  - 형식: `{run, iteration, hypothesis, summary, decision, gain, failed_gate, review, top_changes, ts}`
  - `top_changes`: 측정 `breakdown`에서 직전 best 대비 가장 크게 변한 항목 3개. 시도와 측정 세부를 연결하는 고리다.
  - 최근 N개(기본 15개)를 planner·implementer 프롬프트에 넣는다.

---

## 3. 사용량 할당

### 3.1 측정
- **원시 기록:** 역할 실행마다 `role_done` 이벤트에 다음을 남긴다.
  - `result.usage`의 `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`
  - `duration_ms`, 실제 사용 모델
  - 실행 직전과 직후의 `five_hour` 사용률(참고값)
- **가중 토큰** `wtok = (output + 0.25·cache_creation + 0.1·input + 0.02·cache_read) × tier_factor[tier]`
  - `tier_factor` 기본값: high 5, standard 1, light 0.25
  - 계수는 런 스냅샷 설정값으로 두고, 데이터가 쌓이면 보정한다.
- **역할별 사용량 추정:** 런이 끝나면 아래로 계산해 `role_usage` 이벤트로 남긴다. 화면에는 "사용자 본인 사용 포함 추정치"라고 표시한다.
  - 런 동안 늘어난 사용률의 합 `Σ⁺Δutil` × 역할별 wtok 비중

### 3.2 할당 설정

```yaml
roles:                      # 이 키가 없으면 implementer만 (기존 동작)
  planner:     { enabled: true, when: start_plateau_accept, tier: standard, timeout_min: 10 }
  implementer: { enabled: true, when: always,               tier: standard, timeout_min: 20 }
  reviewer:    { enabled: true, when: on_accept_candidate,  tier: standard, timeout_min: 5 }
allocation:
  mode: manual              # manual | auto
  run_budget_pct: 20        # 이 런이 쓸 5시간 창 사용률 상한(%p), 비우면 제한 없음
  plateau_after: 2          # 연속 '개선 부족' 몇 번이면 정체로 볼지
  attempts_in_prompt: 15
```

- `when` 값
  - planner: `off` / `start_only` / `start_plateau` / `start_plateau_accept`
  - reviewer: `off` / `on_accept_candidate`
- **implementer는 끌 수 없다.**
- **모델 계층 → 모델:** 대시보드 설정의 `model_tiers`가 런 시작 때 스냅샷에 합쳐진다.
  - 기본값: `high: opus`, `standard: sonnet`, `light: haiku` (Claude Code 모델 별칭)
  - 값을 비우면 Claude Code 기본 모델을 쓴다.
  - Spec의 `agent.model`이 있으면 implementer에 한해 그것이 우선한다.

### 3.3 예산 집행 (`run_budget_pct`)
- `rate_limit_event`가 올 때마다 직전 값과 비교해 **양수 증가분만** 누적한다.
  - `resetsAt`이 바뀌거나 값이 크게 내려가면 리셋으로 보고, 그 사이 증가분은 0으로 처리한다.
- 판단은 **반복 시작 전**에만 한다: `남은 예산 < 최근 반복 평균 비용`이면 런을 끝낸다(`run_end.reason = "런 사용량 예산 소진"`).
  - 최근 반복 평균은 최근 3회, 데이터가 없으면 3%p로 본다.
- 기존 `budget.stop_if_five_hour/seven_day`(재개 시각까지 대기)가 **먼저** 적용된다. 예산은 "이 런이 쓸 몫"이고, stop 규칙은 "계정 전체 보호"다.

### 3.4 사용자 토글 (세 단계)
| 단계 | 어디서 | 반영 시점 |
|---|---|---|
| Spec 기본값 | Spec 편집기 "역할 · 할당" (또는 YAML) | 새 런부터 |
| 런 시작 시 덮어쓰기 | 새 런 시작 폼의 역할 토글·계층 선택·할당 모드·예산 | 그 런 (스냅샷에 합쳐짐) |
| 실행 중 변경 | 런 제어 패널 → `set_roles` 명령 | **다음 반복 시작 시**. `roles_changed{by:user, command_id}` |

시작 폼에는 최근 런의 `role_usage`를 근거로 **역할별 예상 비중**을 미리 보여준다. 데이터가 없으면 기본 계수로 추정한다.

---

## 4. 자동 할당 (`allocation.mode: auto`)

### 4.1 1차: 규칙 기반 조정기 (구현)
반복 시작 전에 실행한다. **사용자가 켠 역할과 고른 계층 이하에서만** 조정하고, 바꿀 때마다 `roles_changed{by:auto, reason}`를 남긴다.

| 규칙 | 조건 | 조정 | 되돌림 (히스테리시스) |
|---|---|---|---|
| 정체 탐색 | 연속 '개선 부족' ≥ `plateau_after` | 사용자 설정이 `start_only`라도 planner 1회 실행 | — (일회성) |
| 조기 종료 권고 | 정체 상태에서 planner를 이번 런에 이미 2번 실행 | `stop_recommended` 이벤트 + 텔레그램 (자동 종료는 하지 않음) | — |
| 사용량 압박 | `five_hour ≥ 0.6` 또는 런 예산 70% 소진 | planner·reviewer 계층을 한 단계 내림 (high→standard→light) | `five_hour ≤ 0.3`이 **2번 연속 반복**에서 확인되고, 조정 뒤 2회 이상 지났을 때 복원 |

- 압박 임계값(0.6)은 stop 규칙의 기본값(0.8)보다 낮게 둔다. 대기에 걸리기 전에 비용부터 줄이기 위함이다.

### 4.2 2차: 데이터 기반 (보류, 기반만)
- 1차에서 이벤트(`role_done`, `roles_changed`, `role_usage`, 판정)를 모두 남긴다. 대시보드 ingest가 이를 바탕으로 효율 지표(채택 1건당 사용량)를 계산할 수 있다.
- 조정 규칙 자체의 개선(메타 Spec `loop.allocation`)에는 **런 간 설정을 바꿔 보는 탐색(A/B)** 이 필요하다. 과거 기록을 다시 돌려 보는 것만으로는, 다른 설정이었다면 어땠을지 알 수 없기 때문이다. 런 10~20회분이 쌓인 뒤 착수한다.

---

## 5. 반복 흐름 (변경 후)

```
[반복 시작] 체크포인트(일시정지·stop_after·추가 지시·set_roles) → 예산 확인 → auto 조정기
  ├─ planner (when 충족 또는 가설 소진·낡음)          → 체크포인트
  ├─ implementer (배정 가설 + 시도 기록 + 추가 지시)  → 체크포인트 → 변경 없음·범위 위반 → 기각
  ├─ 평가 스크립트 (gate + objective)
  ├─ 개선폭 > 채택 기준?
  │    ├─ 아니오 → 개선 부족
  │    └─ 예 → reviewer (켜져 있으면) → reject/high: 사람 승인 / pass: 판정 정책 → 채택 또는 승인 대기
  └─ 시도 기록 추가 (규칙 기반)
[런 종료] role_usage
```

**새 이벤트**
- 역할 실행: `role_start{role, tier, model}`, `role_done{role, model, ok, error, duration_ms, usage{4종}, wtok, util_before, util_after}`
- 역할 결과: `hypotheses{items}`, `hypothesis_assigned{iteration, id, title}`, `review{verdict, risk, reasons}`
- 할당: `roles_changed{by, changes, reason, command_id}`, `stop_recommended{reason}`, `role_usage{per_role, total_util}`

**판정:** reviewer는 판정 값을 새로 만들지 않는다. 승인 대기의 `reason`에 검토 결과가 들어간다.

---

## 6. 대시보드 변경 (1차)
| 화면 | 변경 |
|---|---|
| 반복 상세 | 단계 타임라인: planner → implementer → 평가 → reviewer. 단계별 모델, 소요 시간, 가중 토큰, 결과(가설, 검토 사유). 배정 가설 표시 |
| 런 상세 | 역할별 사용량(추정) 막대, 할당 변경 이력, 조기 종료 권고 배너, 가설 후보 목록 |
| 런 제어 패널 | 역할 토글·계층 변경 → `set_roles` |
| 새 런 시작 | 역할 토글·계층·할당 모드·런 예산, 역할별 예상 비중 |
| Spec 편집기 | "역할 · 할당" 섹션 |
| 설정 | 모델 계층 매핑(`model_tiers`), 계층 가중치 |

---

## 7. 호환성
- `roles` 키가 없는 Spec은 **implementer만 켜진 것**으로 해석한다. 기존 동작과 같고, 시도 기록만 추가로 쌓인다.
- 루프는 런 폴더의 `spec.yaml` 스냅샷만 읽는다. 대시보드 설정은 오케스트레이터가 스냅샷에 합친다.

---

## 8. 1차 구현 범위
**포함**
1. `run_role` 일반화: 계층 → 모델, 모델 오류 시 기본 모델로 1회 재실행, 원시 usage 기록, `_agent_proc` 관리, 역할 뒤 체크포인트
2. 런 spec 스냅샷으로 설정 전달 (`model_tiers`, 역할 덮어쓰기)
3. 규칙 기반 Spec 단위 시도 기록 + 프롬프트 주입
4. planner (시작·정체·채택 후, 가설 배정·낡음 처리)
5. 채택 후보 전용 reviewer → 거부 시 사람 승인
6. Spec 기본값 + 런 시작 토글 + 실행 중 `set_roles`
7. `run_budget_pct` (§3.3 방식)
8. auto 규칙 3가지 (§4.1)
9. 대시보드 §6

**보류:** judge, LLM 회고, "모델 확인" 버튼(fallback 경고로 대체), `allocation_log` 테이블과 효율 지표, 메타 Spec, best-of-N

**검증:** 가짜 `claude`로 역할 파이프라인 전체를 확인하고(사용량 0), 측정만 하는 런으로 회귀를 확인한다(사용량 0). 실제 런은 사용자 확인 후.

**예상 비용:** implementer만 쓸 때 반복당 약 2~3%p(첫 런 기준). 1차 기본값(planner는 시작·정체·채택 후, reviewer는 채택 후보만, 둘 다 standard)이면 증가폭은 **10~20%** 로 추정한다(리뷰 에이전트 추정).

---

## 9. 확장 지점
- best-of-N: `roles.implementer.parallel: N` 예약. worktree를 나누고, 평가는 순서대로 하거나 노드를 나눈다.
- 메타 Spec `loop.allocation`: §4.2 데이터 + A/B 탐색.
- judge, LLM 회고: 품질형 대상(문서·코드 품질)을 다룰 때 추가.

## 10. 위험과 대응
| 위험 | 대응 |
|---|---|
| 역할 추가로 비용 증가 | 기본값을 보수적으로, 런 예산, 압박 시 계층 하향 |
| reviewer 오거부 | 버리지 않고 사람 승인으로 넘김 |
| reviewer가 너무 관대 | 검토 통과 후보의 사후 결과(사람 거절·되돌림)를 이벤트로 추적 (2차) |
| 출력 파싱 실패 | 역할별 안전 기본값, 원문 로그 보관 |
| 모델을 쓸 수 없음 | 기본 모델로 재실행, `model_fallback` 경고 |
| 자동 조정 진동 | 되돌림 조건(2회 연속 확인 + 최소 유지 2회), 울타리 |
| 시도 기록 비대화 | 최근 N개만 |

## 11. 1차 구현 검증 (2026-10-25, 가짜 claude·가짜 평가, 사용량 0)
| 경로 | 결과 |
|---|---|
| planner 시작 → 가설 배정 → 채택 후 재계획 → 다음 가설 배정 | ✅ |
| reviewer pass → 자동 채택 / reject → 사람 승인 대기 → 승인 후 채택 | ✅ |
| 실행 중 `set_roles` → 다음 반복에 반영, `command_id` 기록 | ✅ |
| auto: 사용량 압박(68%) → 보조 역할 계층 하향 | ✅ |
| 모델 사용 불가 → 기본 모델로 재실행 + `model_fallback` | ✅ |
| `run_budget_pct` → 남은 예산 < 반복 평균 비용에서 종료 | ✅ |
| 정체 → 재분석(최대 2회) → `stop_recommended`, 이후 재분석 중단 | ✅ (검증 중 발견한 "매 반복 재분석" 버그 수정 후) |
| `role_usage` 추정, 시도 기록(`top_changes` 포함) | ✅ |

검증 중 고친 문제:
- 재분석한 가설의 id가 이전과 겹치면 모두 사용된 것으로 처리돼 매 반복 planner가 다시 도는 문제. 분석 회차별 고유 id, 제목 중복 판정, "새 가설을 냈을 때만 소진 재분석"으로 해결했다.
- 기본 모델로 대체 실행했는데도 가중 토큰을 원래 계층 계수로 계산하던 문제.
- 조기 종료 권고 뒤에도 재분석하던 문제.

실제 Claude로 하는 검증(실제 모델 사용 가능 여부, 출력 형식 준수율, 반복당 실제 비용)은 사용자 확인 후 진행한다.
