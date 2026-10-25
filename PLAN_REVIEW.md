# RSI Loop 플랫폼 — 타당성 검토 및 설계 계획 (v2.1)

> 작성일: 2026-10-25 · 개정: v2.1 (홈서버 중심 + SSH 실행 노드, Pro 플랜 운용, Astro_Manager 파일럿)
> 대상 환경: 홈서버 (Rocky Linux 8.10, 8 core, RAM 15GB, Docker, Cloudflare Tunnel 운영 중)

---

## 0. 요약

| 목표 | 타당성 | 핵심 조건 |
|---|---|---|
| 홈서버 웹 대시보드 & 콘솔 (외부 접속) | ✅ 가능 | 기존 Cloudflare Tunnel에 서비스 1개 추가 (포트 35004) |
| 여러 머신에서 RSI Loop 실행 (Linux·Windows·Pi) | ✅ 가능 | **홈서버가 루프 본체(에이전트)**, 나머지 머신은 SSH로 붙는 **실행 노드**(빌드·테스트·벤치마크) |
| 실시간 모니터링 + 중단/변경/추가요청 | ⚠️ 조건부 | 반영 시점을 즉시 / 단계 경계 / 다음 반복으로 구분 |
| **엔진 = Claude Code** | ✅ 가능 | 헤드리스 모드(`claude -p`) 래핑. **사용량 한도가 핵심 제약** → 한도를 고려한 스케줄러가 필요 |
| **대화형으로 개선대상·평가지표 설계** | ✅ 가능, 단 가장 큰 작업 | "설계 계층"을 실행 루프와 분리된 1급 기능으로 설계 |

**핵심 결론**
1. 이 시스템은 단순한 "루프 모니터"가 아니라 **2계층 플랫폼**이다.
   - **설계 계층**: 무엇을 개선할지, 어떻게 평가할지, 누가 판단할지를 대화로 정의한다.
   - **실행 계층**: 정의된 대로 루프를 돌리고 관측·개입한다.
2. Claude Code 구독 사용량은 **계정 단위로 공유**된다. 현재 Pro 플랜이므로 **사용량 자체를 스케줄링 대상 자원**으로 다뤄야 한다. 에이전트를 홈서버 한 곳에서만 돌리면 로그인·사용량 관리가 한 곳에 모인다.
3. 규모가 크므로 **실행 계층을 먼저, 설계 계층은 처음엔 단순하게** 시작하는 단계적 접근을 권장한다(11장).

---

## 1. 시스템 개념

### 1.1 두 계층

```
┌─────────────── 설계 계층 (Design Studio) ───────────────┐
│  대화 ──▶ 개선대상(Target) 정의                            │
│        ──▶ 평가지표(Metric) 설계 ──▶ 지표 검증(Baseline)    │
│        ──▶ 판단 정책(Judge Policy) 결정 ──▶ 승인 → Spec 확정 │
└──────────────────────────┬──────────────────────────────┘
                           │ Spec (버전 관리됨)
┌──────────────────────────▼──────────────────────────────┐
│                실행 계층 (Loop Engine)                     │
│  계획 → 실행 → 평가(Metric) → 판단(Judge) → 채택/폐기 → 회고 │
│        ▲                                    │            │
│        └──────────── 다음 반복 ◀──────────────┘            │
└─────────────────────────────────────────────────────────┘
```

### 1.2 핵심 개념 정의

| 개념 | 의미 | 예시 |
|---|---|---|
| **Project** | 작업 공간 (보통 저장소 1개) | `ETF_Manager` |
| **Target** | 이번에 개선할 대상. 한 프로젝트에 여러 개 가능 | "리밸런싱 계산 모듈 정확도", "README 품질", "에이전트용 CLAUDE.md" |
| **Metric** | Target의 개선 여부를 수치로 판정하는 방법 | 테스트 통과율, 벤치마크 시간, LLM 채점 루브릭 점수 |
| **Judge Policy** | 반복 결과를 **누가·어떤 기준으로** 채택할지 | 자동(점수 상승 시) / 에이전트 판단 / 사람 승인 / 혼합 |
| **Spec** | Target + Metric + Judge Policy + 예산·제약을 묶은 **버전 관리되는 정의서** | `spec v3` |
| **Run** | 하나의 Spec으로 루프를 실행하는 단위 | |
| **Iteration** | Run 안의 한 번의 반복 | |
| **Epoch** | Run 도중 Spec이 바뀌면 새 Epoch 시작 (점수 비교 기준이 바뀌므로 구분) | |
| **Design Session** | Spec을 만들거나 수정하기 위한 대화 세션 | |

---

## 2. 아키텍처

### 2.0 역할 분담 (v2.1 확정)
- **홈서버 = 두뇌.** 대시보드, 오케스트레이터, Claude Code 에이전트 세션이 모두 홈서버에서 돈다. 코드 원본(git worktree)도 홈서버에 있다.
- **실행 노드 = 손발.** Windows PC(GPU), 홈서버 자신(Linux), 라즈베리파이(ARM·경량화) 등. 홈서버가 **SSH로 접속해서** 코드를 동기화하고 빌드·테스트·벤치마크를 실행한 뒤 결과를 회수한다. 노드에는 상주 프로그램이 필요 없고, SSH 서버와 개발 환경만 있으면 된다.

**루프 관리 부하:** 오케스트레이터는 프로세스 관리와 이벤트 저장만 하므로 가볍다. 무거운 것은 Claude Code 세션(1개당 수백 MB)과 테스트·벤치마크 실행인데, 동시 실행 1개 기준이면 홈서버로 충분하다. GPU 작업은 Windows 노드로 보낸다.

```
┌──────────────────────────── 홈서버 (Rocky Linux) ────────────────────────────┐
│  Web Dashboard (React) ◀─SSE/WS─▶ API Server (FastAPI)                        │
│                                     ├─ Spec / Run / Event 저장 (SQLite)         │
│                                     ├─ Command Queue · Usage Scheduler          │
│                                     └─ Telegram Notifier                        │
│  Orchestrator                                                                  │
│   ├─ Claude Code 세션 (Design / Loop) ← 구독 로그인은 여기 한 곳                   │
│   ├─ Workspace: 반복마다 git worktree                                           │
│   ├─ Node Manager: SSH 연결 풀, 노드 상태 확인, 코드 동기화                        │
│   └─ Eval Dispatcher: 지표를 노드별로 실행하고 결과 수집                           │
└───────────┬──────────────────────┬──────────────────────┬────────────────────┘
            │ (로컬 실행)            │ SSH (LAN)             │ SSH (LAN)
   ┌────────┴───────┐     ┌────────┴────────┐     ┌───────┴────────┐
   │ node: server   │     │ node: win-gpu   │     │ node: rpi      │
   │ linux / x86_64 │     │ windows / GPU   │     │ linux / arm64  │
   │ 기본 테스트       │     │ Windows 호환·GPU │     │ 경량화 검증       │
   └────────────────┘     └─────────────────┘     └────────────────┘
```

### 2.1 연결 방향
- 사용자 → 홈서버: 웹 대시보드 (Cloudflare Tunnel, 2.3절). 출근길 휴대폰 접속도 동일하다.
- 홈서버 → 노드: SSH. 노드는 모두 집 LAN 안에 있으므로 홈서버에서 바로 접속할 수 있다.
- 노드가 꺼져 있으면 해당 노드가 필요한 평가만 `waiting_node` 상태로 대기하고, 대시보드와 텔레그램에 알린다.

### 2.2 실행 노드 관리

**노드 등록 정보 (대시보드 Nodes 화면에서 관리)**

```yaml
nodes:
  - id: server
    transport: local
    os: linux
    arch: x86_64
    tags: [default]
  - id: win-gpu
    transport: ssh
    host: 192.168.x.x
    user: heejo
    shell: powershell
    os: windows
    arch: x86_64
    tags: [gpu, cuda, windows]
    workdir: D:/rsi-work
  - id: rpi
    transport: ssh
    host: 192.168.x.y
    os: linux
    arch: arm64
    tags: [arm, lowpower]
```

**Spec에서 노드 지정**
```yaml
eval:
  matrix:                       # 같은 지표를 여러 환경에서 돌림 → 모두 통과해야 gate 통과
    - { node: server }
    - { node: win-gpu }
  requires: [gpu]               # 이 태그를 가진 노드만 사용 (objective 측정용)
```

**코드 동기화:** 홈서버의 반복 worktree를 커밋 → 노드에서 `git fetch` + checkout (홈서버를 git remote로 등록). rsync보다 추적이 쉽고 Windows에서도 동일하게 동작한다.

**에이전트의 노드 사용 — 두 가지 모드**
| 모드 | 방식 | 용도 |
|---|---|---|
| 평가 (기본) | 오케스트레이터가 정해진 명령만 노드에서 실행 | gate/objective 측정. 에이전트가 개입하지 못하므로 조작 불가 |
| 디버깅 (선택) | 에이전트에게 `node-exec <node> <cmd>` 래퍼 도구만 허용 (`--allowedTools "Bash(node-exec:*)"`) | "Windows에서만 실패" 같은 문제를 에이전트가 직접 조사 |

**Windows 노드 준비 사항**
- Windows 기본 제공 **OpenSSH Server** 활성화, 키 인증만 허용 (패스워드 로그인 끄기).
- git, Python, CUDA 등 개발 환경. 기본 셸은 PowerShell로 지정.
- 저장소에 `.gitattributes`로 줄바꿈 규칙 고정 (CRLF 문제 예방).
- 주의: SSH 세션에서는 **GUI 프로그램 테스트가 불가**하다 (데스크톱 세션이 없음). CUDA나 CLI 작업은 문제없다. GUI 검증이 필요하면 별도 방식(예약 작업으로 사용자 세션에서 실행)을 검토해야 한다.
- 절전 모드에 들어가면 노드가 사라지므로 전원 설정 또는 Wake-on-LAN을 고려한다.

**라즈베리파이:** 일반 Linux SSH 노드와 같다. 측정이 느리므로 매 반복이 아니라 **후보가 확정됐을 때만** 돌리도록 설정할 수 있게 한다 (`run_on: final_only`).

### 2.3 외부 접속 — 기존 Cloudflare Tunnel 활용 (확정)

| 기존 서비스 | 컨테이너 | 호스트 포트 | 위치 |
|---|---|---|---|
| ETF Manager | etf-manager-app | 35000 | ~/ETF_Manager |
| MViewer (zippic) | zippic-app | 35001 | ~/MViewer |
| AViewer | aviewer | 35002 | /data1/Python/AViewer |
| Astro Manager | astro-manager | 35003 | ~/Astro_Manager |
| **RSI Loop (신규)** | **rsi-loop** | **35004** (미사용 확인) | **~/RSI_Loop** |

- `cloudflared`는 토큰 방식(원격 관리형)으로 운영 중이다. **Cloudflare Zero Trust 대시보드 > Tunnels > Public Hostname**에 `rsi.<도메인>` → `http://localhost:35004`를 추가한다.
- 배포는 Astro Manager와 같은 패턴을 쓴다: `docker-compose.yml` + `.env`(git 제외) + `restart: unless-stopped`.
- WebSocket은 터널을 통과한다. 다만 유휴 연결이 끊길 수 있으므로 **30초 heartbeat와 자동 재연결**을 넣는다.
- 에이전트가 홈서버에서 돌기 때문에 러너용 외부 접속 경로는 필요 없다. 터널로 노출되는 것은 사람용 대시보드뿐이다.

---

## 3. 에이전트 엔진: Claude Code

### 3.1 가능 여부 — ✅ 가능
Claude Code는 **헤드리스(비대화형) 모드**를 지원하므로 Runner가 프로세스로 띄워 제어할 수 있다.

| 필요 기능 | Claude Code에서의 수단 |
|---|---|
| 자동 실행 | `claude -p "<프롬프트>"` |
| 진행 상황 실시간 수집 | `--output-format stream-json` → 메시지·툴 호출·결과가 JSON 줄 단위로 출력되므로 그대로 서버 이벤트로 변환 |
| 대화 이어가기 (설계 세션, 추가 지시) | `--resume <session_id>` / `--continue`, 또는 `--input-format stream-json`으로 입력 스트리밍 |
| 모델 선택 | `--model` (단계별로 다른 모델 사용 가능) |
| 권한·안전 제한 | `--allowedTools` / `--disallowedTools`, `--permission-mode`, 프로젝트 `.claude/settings.json` |
| 루프 지침 주입 | `--append-system-prompt`, 프로젝트 `CLAUDE.md` |
| 체크포인트에서 개입 | **Hooks** (`Stop`, `PreToolUse`, `PostToolUse`, `UserPromptSubmit` 등): 단계 경계에서 서버 명령 큐를 확인하고 추가 지시를 주입하거나 중단 |
| 즉시 중단 | 프로세스에 SIGINT/SIGTERM |

**참고 사항**
- 현재 이 서버에는 Claude Code가 VSCode 확장 안에만 있고 `claude` CLI가 PATH에 없다. 홈서버에 CLI를 설치하고 로그인해야 한다.
- 대시보드 컨테이너와 Claude Code의 위치: Claude Code는 로그인 정보(`~/.claude`)와 git 작업 공간에 접근해야 하므로, **오케스트레이터는 호스트에서 systemd 서비스로** 돌리고 웹 API만 컨테이너로 두는 구성이 단순하다. 둘은 로컬 소켓이나 내부 HTTP로 통신한다.
- **Claude Agent SDK보다 CLI 래핑을 권장한다.** Agent SDK는 공식적으로 API 키 인증·종량 과금을 기준으로 안내된다. 구독(Pro/Max) 사용량으로 돌리려면 사용자가 직접 로그인한 `claude` CLI를 래핑하는 쪽이 안전하다. 착수 전에 최신 이용 약관과 인증 정책을 한 번 확인할 것.

### 3.2 사용량 한도 대응 — 가장 중요한 운영 제약

**전제:** 구독 사용량은 계정 단위다. 한도는 짧은 주기(수 시간 단위)와 주간 단위로 걸린다. **현재 Pro 플랜**이고, 평소 직접 쓰는 사용량과 같은 풀을 나눠 쓴다.

**Pro 플랜 기본 운용값 (절약 모드)**
| 항목 | 기본값 | 비고 |
|---|---|---|
| 동시 실행 런 | 1 | |
| 반복당 에이전트 턴 상한 | `--max-turns`로 제한 (예: 30) | 폭주 방지 |
| 기본 모델 | 경량·중간 모델 위주, 계획·회고만 상위 모델 | Pro에서 쓸 수 있는 모델은 착수 시 확인 |
| 루프 사용 비중 상한 | 주기당 50% 이하 (사용자 설정) | 본인 작업용 여유 확보 |
| 한도 도달 시 | 자동 Pause → 재개 시각에 자동 Resume | |
| 실행 시간대 | 야간·근무 시간 예약 | 직접 쓰는 시간대 회피 |

**남은 사용량 파악 — CLI에서 받을 수 있다 (2026-10-25, Claude Code 2.1.310 기준 확인)**

VSCode 패널의 "Session (5hr) 19% / Weekly (7 day) 37%"는 공개 REST API가 아니라 **Claude Code CLI가 내보내는 사용량 정보**를 표시한 것이다. 같은 정보를 우리 오케스트레이터도 받을 수 있다.

| 경로 | 내용 | 용도 |
|---|---|---|
| **헤드리스 출력의 `rate_limit_event`** | `claude -p --output-format stream-json` 실행 중 사용량 상태가 바뀌면 `{"type":"rate_limit_event","rate_limit_info":{...}}` 메시지가 출력된다 | **루프 실행 중 주 경로.** 이벤트를 받아 DB에 저장하고 대시보드 게이지로 표시 |
| **statusline 입력 JSON** | 대화형 세션의 statusline 명령에 `rate_limits.five_hour.used_percentage`, `rate_limits.seven_day.used_percentage`, `resets_at`이 전달된다 | 사용자가 직접 쓰는 대화형 세션의 사용량도 기록하고 싶을 때 statusline 스크립트가 서버로 전송 |
| 한도 도달 에러 | 한도에 걸리면 에러에 어느 한도인지와 재개 시각이 포함된다 | 자동 Pause → 재개 시각에 자동 Resume |

- 장점: 퍼센트와 재개 시각이 **실제 계정 기준 값**이라 추정할 필요가 거의 없다. 5시간 주기와 7일 주기를 둘 다 알 수 있다.
- 주의: 공식 문서화된 공개 API가 아니므로 CLI 버전이 바뀌면 필드가 달라질 수 있다. 파싱은 엔진 어댑터 안에 격리하고, 필드가 없으면 자체 토큰 집계로 대체(fallback)한다.
- 스케줄러 규칙 예: `five_hour ≥ 80%`면 새 반복을 시작하지 않고 대기, `seven_day ≥ 70%`면 루프 비중 자동 축소 → 텔레그램 알림.
- 한계: 루프가 아무것도 실행하지 않는 동안에는 새 값이 들어오지 않는다. 이때는 마지막 값과 재개 시각을 기준으로 표시하고 "마지막 갱신 시각"을 같이 보여준다.

**플랜 업그레이드 판단 근거:** 대시보드에 **"사용량 대비 개선량"**(예: 사용량 1단위당 속도 개선 %)과 한도 때문에 멈춘 시간을 기록한다. 이 데이터로 Max 전환이 이득인지 판단할 수 있다.

| 대응 | 내용 |
|---|---|
| **사용량 인지 스케줄러** | 서버가 모든 런의 실행 순서를 정한다. 동시 실행 수 제한(기본 1)과 우선순위 큐를 둔다 |
| **한도 도달 자동 처리** | Claude Code 출력에서 한도 도달 메시지나 에러를 감지하면 해당 런을 `rate_limited` 상태로 Pause하고, 재개 가능 시각에 자동 Resume. 텔레그램 알림 |
| **사용량 미터** | 반복별 토큰·소요 시간을 수집해 대시보드에 "이번 주기 사용 추정치"를 표시. 런별 소비 비중도 보여준다 |
| **모델 계층화** | 계획·회고·평가 설계처럼 판단이 중요한 단계는 상위 모델, 반복 구현·로그 요약은 경량 모델 |
| **컨텍스트 절약** | 반복마다 새 세션을 시작한다. 이전 반복은 **요약본만 주입**하고 긴 로그는 파일로 넘긴다 (세션을 무한히 이어가면 비용이 급증) |
| **저비용 평가 우선** | 테스트·정적 분석 같은 비-LLM 평가를 먼저 돌리고, LLM 채점은 통과한 후보에만 |
| **시간대 운영** | 사용자가 직접 Claude를 쓰는 시간대와 겹치지 않게 야간·외출 시간에 런을 몰아서 배치하는 일정 기능 |

### 3.3 엔진 어댑터 — 미래 확장 (Thor 등 로컬 서버)
Runner는 엔진을 인터페이스로 감싼다: `start / send / interrupt / resume / parse_events`.
- 현재: `ClaudeCodeEngine` (구독 CLI)
- 이후 선택지: API 키 기반 Claude, 또는 로컬 LLM 서버(Jetson Thor 등). Claude Code도 Anthropic 호환 게이트웨이를 통해 다른 백엔드를 가리킬 수 있으므로 어댑터만 추가하면 된다.
- **엔진별 사용량·성능을 같은 대시보드에서 비교**할 수 있게 이벤트 스키마에 `engine`, `model` 필드를 처음부터 넣어둔다.

---

## 4. 설계 계층 — 대화형 개선대상·평가지표 설계

이 시스템의 차별점이자 가장 큰 작업이다.

### 4.1 Spec 생애주기

```
Draft ─▶ Designing ─▶ Validating ─▶ Ready ─▶ Running ─▶ Reviewing ─▶ Archived
 (생성)   (대화로 정의)  (지표 검증)   (승인)    (루프)     (결과 검토)
              ▲              │                    │
              └── 지표 부적합 ─┘                    └─ Spec 수정 → 새 버전 / 새 Epoch
```

1. **Designing (대화):** 대시보드 채팅창 ↔ Runner의 Design Session(Claude Code)이 연결된다. 에이전트가 저장소를 읽고 질문하면서 Target 후보를 제안하고, 사용자가 고른다.
2. **지표 설계:** 에이전트가 Target에 맞는 Metric 후보 여러 개를 장단점과 함께 제시한다. 측정 비용, 자동화 가능성, 조작 가능성을 비교한다.
3. **Validating (지표 검증):** 정한 지표를 **현재 상태(Baseline)에 실제로 돌려본다.**
   - 결과가 매번 같은가(재현성), 좋은 변경과 나쁜 변경을 구분하는가(변별력), 1회 측정에 비용·시간이 얼마나 드는가.
   - 통과하지 못하면 Designing으로 돌아간다.
4. **Ready (승인):** 사용자가 Spec을 확정한다. 이 시점의 Spec이 버전으로 고정된다.

### 4.2 Target 유형 카탈로그 (초기 템플릿)

| 유형 | 예시 | 대표 지표 | 위험도 |
|---|---|---|---|
| 코드 기능·정확도 | 버그 수정, 기능 구현 | 테스트 통과율, 회귀 없음 | 낮음 |
| 코드 성능 | 처리 속도, 메모리 | 벤치마크 수치 | 낮음 |
| 코드 품질 | 리팩터링, 타입 안정성 | 린트·타입 오류 수, 복잡도 | 중간 (지표 조작 쉬움) |
| 문서·콘텐츠 | README, 가이드 | LLM 루브릭 채점 + 사람 샘플 검토 | 중간 |
| 에이전트 프로세스 | CLAUDE.md, 스킬, 프롬프트 | 고정된 과제 세트의 성공률·소요 사용량 | **높음 (자기 수정)** |
| 데이터·설정 | 파라미터 튜닝 | 목적 함수 값 | 낮음~중간 |

대화 시 템플릿을 출발점으로 쓰되, 자유 정의도 허용한다.

### 4.3 Metric 설계 규칙
- **형태:** `측정 명령(또는 채점 프롬프트) → 수치 점수 + 근거`. 여러 지표를 쓰면 가중합 또는 "필수 조건(gate) + 최적화 대상(objective)"으로 구성한다.
  예: `gate: 기존 테스트 100% 통과` + `objective: 응답 시간 최소화`
- **LLM 채점 지표 주의점**
  - 작업한 세션과 **분리된 새 세션**에서 채점한다 (자기 작업 편애 방지).
  - 루브릭은 Spec에 고정한다. 같은 결과를 2~3회 채점해 편차를 확인한다.
  - 루프가 보지 못하는 **보류 세트(held-out)**를 두어 과적합을 점검한다.
- **조작 방지 (Goodhart):** 평가 스크립트, 테스트, 루브릭 파일은 루프 에이전트가 **수정할 수 없는 경로**에 두고 권한으로 차단한다.

### 4.4 Judge Policy — 누가 판단하는가

| 정책 | 채택 조건 | 적합한 경우 |
|---|---|---|
| `auto` | gate 통과 + objective 개선 | 수치가 명확한 코드 성능·정확도 |
| `agent` | 별도 판단 에이전트가 근거와 함께 채택/폐기 결정 | 수치만으로 판단이 어려운 품질 개선 |
| `human` | 매 반복 사용자 승인 (텔레그램 알림 → 모바일 승인) | 위험도 높은 대상, 초기 탐색 |
| `hybrid` (권장 기본값) | 평소엔 auto/agent, 다음 조건에서만 사람 호출: 점수 급변, 큰 변경량, 지침 파일 수정, N회마다 샘플 검토 | 대부분 |

대시보드는 모든 판단에 **판단 주체, 근거, 점수 변화**를 기록하고, 사람이 사후에 판단을 뒤집을 수 있게 한다(뒤집으면 해당 반복 롤백).

### 4.5 Spec 예시 (개념)

```yaml
spec: etf-rebalance-accuracy
version: 3
project: ETF_Manager
target:
  type: code.correctness
  description: 리밸런싱 계산의 경계 조건 오류 제거
  editable_paths: [app.py, lib/rebalance/**]
  protected_paths: [tests/**, eval/**]
metric:
  gates:
    - name: unit_tests
      cmd: pytest -q tests/
      pass: "exit_code == 0"
  objective:
    name: edge_case_suite
    cmd: python eval/edge_cases.py --json
    direction: maximize
  baseline: { edge_case_suite: 0.71, measured_at: 2026-10-25 }
judge:
  policy: hybrid
  human_review_when: [score_jump > 0.15, diff_lines > 300, every_n: 5]
budget:
  max_iterations: 20
  max_wall_time: 6h
  usage_share: 40%        # 이번 주기 사용량 중 이 런이 쓸 수 있는 비중
engine: { name: claude-code, plan_model: <상위 모델>, work_model: <경량 모델> }
```

---

## 5. 실행 계층 — 루프와 실시간 개입

### 5.1 반복 단계와 체크포인트
`① 계획` → **CP** → `② 실행(코드 수정)` → **CP** → `③ 평가` → `④ 판단` → **CP** → `⑤ 채택(병합)/폐기` → `⑥ 회고(다음 반복 메모)` → **CP**

### 5.2 개입 명령

| 명령 | 반영 시점 | 구현 |
|---|---|---|
| ⏹ Stop | 즉시 | 프로세스 SIGINT, 반복은 `aborted`, 작업 브랜치 보존 |
| ⏸ Pause / ▶ Resume | 다음 CP | Hook 또는 Orchestrator가 CP에서 큐 확인 |
| ➕ Inject (추가 지시) | 다음 단계 시작 | 다음 프롬프트에 사용자 지시로 포함 |
| ✏️ Modify Spec | 다음 반복 (새 Epoch) | Design Session을 열어 수정 → 승인 → Spec 새 버전 |
| ✅ Approve / ❌ Reject | 판단 단계 | Judge Policy가 사람을 호출한 경우 |
| ↩️ Revert | 즉시 (런 Pause 후) | 특정 반복의 채택 취소, 해당 시점으로 롤백 |

명령 상태는 `queued → delivered → applied`로 대시보드에 표시하고, 각 명령에 "언제 반영되는지"를 명시한다.

---

## 6. 안전장치

1. **예산:** 반복 수, 실행 시간, 사용량 비중 상한. 초과 시 자동 Pause.
2. **격리:** 반복마다 `git worktree`. main 병합은 Judge 채택 시에만.
3. **보호 경로:** 평가 스크립트, 테스트, 루브릭, Spec 파일은 루프 에이전트 쓰기 금지 (`--disallowedTools` 및 경로 규칙).
4. **정체·발산 감지:** N회 연속 개선 없음, 동일 오류 반복, 점수 급락 → Pause + 알림.
5. **자기 수정 Target은 `human` 또는 `hybrid` 강제:** 에이전트 지침이나 스킬을 바꾸는 대상은 사람 승인 없이 채택하지 않는다.
6. **권한 최소화:** 루프 세션에 허용 툴을 화이트리스트로 지정. 네트워크·시스템 명령은 기본 차단.
7. **명령 화이트리스트:** 대시보드에서 노드에 **임의 셸 명령을 보내는 기능은 만들지 않는다.** 정해진 명령 타입만 보낸다.
8. **감사 로그:** 모든 명령, 판단, Spec 변경 이력을 기록한다.

---

## 7. 데이터 모델 (초안)

```
Project        (id, name, repo_path, description)
Node           (id, transport[local|ssh], host, user, os, arch, tags, workdir, last_seen, status)
Spec           (id, project_id, name, version, status, target_json, metric_json,
                judge_json, budget_json, engine_json, baseline_json, created_by, approved_at)
DesignSession  (id, spec_id, engine_session_id, status, transcript_ref)
Run            (id, spec_id, spec_version, status, started_at, ended_at)
EvalResult     (id, iteration_id, node_id, metric_name, value, raw_ref, duration, ts)
Epoch          (id, run_id, spec_version, started_at_iteration)
Iteration      (id, run_id, epoch_id, index, phase, status, scores_json,
                diff_ref, summary, usage_json, started_at, ended_at)
Judgment       (id, iteration_id, judge_type[auto|agent|human], decision,
                rationale, overridden_by, ts)
Event          (id, run_id|design_session_id, iteration_id, seq, type, payload_json, ts)
Command        (id, target_ref, type, payload, status, issued_by, issued_at,
                delivered_at, applied_at)
UsageSnapshot  (id, ts, window, estimated_used, source)
Artifact       (id, owner_ref, kind, path)      # 로그 원문, diff, 리포트, 대화 기록
```

- Event는 추가만 하고 수정하지 않는다. 생성 측이 붙이는 seq 번호로 중복을 제거한다.
- 대용량 데이터(로그, diff, 대화 원문)는 파일로 저장하고 DB에는 참조만 둔다.

---

## 8. 대시보드 정보 구조

"기반부터 실제 루프까지"를 체계적으로 보여주기 위해 **계층형 내비게이션 + 각 계층의 상태 요약**으로 구성한다.

### 8.1 계층 구조
```
Overview (전체 현황)
 └ Project
    ├ Specs ── Spec 상세 (Target · Metric · Judge · 버전 이력 · Baseline)
    │           └ Design Session (대화)
    └ Runs ─── Run 상세
                └ Iteration 상세 (단계별 로그 · diff · 점수 · 판단 근거)
System: Nodes · Usage · Notifications · Settings · Audit Log
```

### 8.2 주요 화면

| 화면 | 표시 정보 | 실시간 갱신 |
|---|---|---|
| **Overview** | 실행 중 런, 승인 대기 건수, 노드 상태, 이번 주기 사용량 게이지, 최근 이벤트 | ✅ |
| **Spec 상세** | Target 정의, 지표 구성(gate/objective), Judge 정책, Baseline, 버전 간 차이 | 변경 시 |
| **Design Studio** | 좌: 대화 / 우: 대화로 채워지는 Spec 초안(실시간 반영) + 지표 검증 결과 | ✅ |
| **Run 상세** | 반복 타임라인, 점수 추이 차트(Epoch 구분선), 현재 단계, 실시간 로그, 사용량, 명령 콘솔 | ✅ |
| **Iteration 상세** | 단계별 에이전트 메시지·툴 호출, diff, 각 지표 결과, 판단 주체와 근거 | 진행 중이면 ✅ |
| **Approvals** | 사람 판단 대기 목록 (모바일 최적화) | ✅ |
| **Usage** | 시간대별 사용량, 런별 소비 비중, 한도 도달 이력, 예약 스케줄 | ✅ |
| **Nodes** | 노드별 연결 상태(SSH 확인), OS·태그, 현재 실행 중인 평가, 최근 결과 | ✅ |

### 8.3 반응형 (PC + 모바일)
- PC: 다단 레이아웃 (내비게이션 · 본문 · 상세 패널).
- 모바일: **요약 → 판단 → 이탈이 30초 안에** 끝나는 구조. 하단 고정 버튼 바(⏹ ⏸ ➕), 승인 화면 우선, 실시간 로그는 기본 접힘.
- 선택: PWA로 홈 화면에 추가.

### 8.4 실시간 갱신 방식
- 서버 → 브라우저: SSE (또는 WS) 하나로 구독. 화면별로 필요한 채널만 구독한다 (`run:<id>`, `overview`).
- 고빈도 이벤트(툴 호출, 로그)는 서버에서 묶어서 보내고(예: 500ms), 모바일은 요약 이벤트만 받는다.

---

## 9. 기술 스택 & 배포

| 영역 | 선택 |
|---|---|
| API Server | **FastAPI** + WebSocket/SSE (서버에 Python 존재, Astro Manager와 동일) |
| DB | **SQLite** (WAL 모드), 필요 시 Postgres로 이전 |
| 대시보드 | **React + Vite** (Astro Manager와 동일), 정적 빌드를 FastAPI가 서빙 |
| 차트 | 경량 차트 라이브러리 (예: Recharts / uPlot) |
| Orchestrator | 홈서버 호스트의 Python 서비스(systemd) + **Claude Code CLI 래핑** (엔진 어댑터 구조) |
| 노드 연결 | SSH (Python `asyncssh` 또는 시스템 `ssh` + ControlMaster), 키 인증 |
| 배포 | 웹 API·대시보드는 Docker Compose(포트 35004, `.env` 분리), 오케스트레이터는 systemd |
| 네트워크 | 기존 Cloudflare Tunnel에 Public Hostname 추가 |
| 인증 | 패스워드(werkzeug 해시, Astro Manager 방식) + **TOTP(pyotp)** + 세션 쿠키 |
| 알림 | 텔레그램 봇 |

**서버 자원:** RAM 15GB 중 약 11GB 사용 중이다. 대시보드 스택은 수백 MB 수준이다. 홈서버가 에이전트도 돌리므로 Claude Code 세션과 테스트 실행 부하가 추가된다.
- 확인된 사항: `setroubleshootd`(SELinux 알림 데몬)가 **약 5.4GB**를 점유하고 있다. 이 데몬은 메모리를 계속 쌓는 것으로 알려져 있어, 재시작하거나 불필요하면 비활성화하면 수 GB를 확보할 수 있다 (sudo 필요). 그다음으로 큰 것은 `uvicorn` 프로세스 하나(약 2.5GB)다.

---

## 10. 알림 & 인증

### 10.1 텔레그램
- @BotFather로 봇 생성 → 토큰과 chat_id를 `.env`에 저장. 서버가 `sendMessage`를 호출한다(바깥으로만 요청하므로 포트 개방 불필요).
- 알림 이벤트:

| 이벤트 | 긴급도 |
|---|---|
| 사람 판단 대기 (Approve 필요) | 🔴 |
| 자동 Pause (예산, 정체, 점수 급락) | 🔴 |
| 사용량 한도 도달 / 재개 예정 시각 | 🟡 |
| 런 실패, 필요한 노드 오프라인 | 🟡 |
| Design Session에서 사용자 답변 대기 | 🟡 |
| 런 완료, 목표 달성 | 🟢 |

- 메시지에 `https://rsi.<도메인>/...` 딥링크를 포함한다.
- (후순위) 인라인 버튼으로 승인/중단. chat_id 화이트리스트로 본인만 허용한다.

### 10.2 패스워드 + TOTP
- TOTP는 표준(RFC 6238)이라 **개인 서비스에서도 외부 가입·비용 없이** Google Authenticator, 1Password, Bitwarden 등으로 쓸 수 있다. `pyotp` + `qrcode`로 구현한다.
- 흐름: 최초 QR 등록 → 로그인 시 패스워드 + 6자리 코드.
- 필수 사항: 복구 코드 8~10개 발급, 세션 쿠키 `HttpOnly`/`Secure`/`SameSite=Lax`, 로그인 실패 5회 시 잠금, 관리 작업(토큰 발급, 설정 변경) 시 OTP 재입력.
- 선택: Cloudflare Access를 앞에 둬서 이중 보호.

---

## 11. 규모 산정 & 로드맵

전체 범위는 **개인 프로젝트로는 큰 규모**다(수개월 단위). 각 단계가 끝날 때마다 **그 자체로 쓸모 있는 상태**가 되도록 나눈다.

| Phase | 내용 | 끝나면 할 수 있는 것 |
|---|---|---|
| **0. 루프 원형** | 대시보드 없이 홈서버에서 스크립트로: Claude Code 헤드리스 + Spec YAML 수기 작성 + 평가 + git worktree + JSONL 이벤트 로그. 사용량 한도 감지 포함 | 손으로 쓴 Spec 하나로 실제 RSI 루프가 돈다 |
| **1. 관측** | 서버(FastAPI, SQLite, 인증+TOTP), 이벤트 수집, Overview·Run·Iteration·Nodes 화면, 터널 연결 | 외출 중 휴대폰으로 진행 상황 확인 |
| **2. 제어 + 알림** | 명령 큐(Stop, Pause, Resume, Inject), 텔레그램 알림, Approvals 화면, Judge `human`/`auto` | 외출 중 중단, 추가 지시, 승인 |
| **3. 설계 계층 v1** | Spec 편집 UI(폼 기반) + 버전 관리 + 지표 검증(Baseline 실행) + Epoch | 대시보드에서 Spec을 만들고 고친다 |
| **4. 설계 계층 v2 (대화형)** | Design Studio: 대시보드 채팅 ↔ Claude Code 세션 중계, 대화로 Spec 초안 자동 작성, Target 템플릿, Judge `agent`/`hybrid` | **목표했던 "대화로 대상·지표 설계"** |
| **5. 운영 고도화** | 사용량 스케줄러(우선순위, 시간대 예약), Windows·Pi 노드 매트릭스 평가, 런 비교, 엔진 어댑터 추가 | 여러 프로젝트 병행, 향후 로컬 LLM 서버 편입 |

**권장:** Phase 3(폼 기반 Spec)을 Phase 4(대화형)보다 먼저 둔다. 대화형 설계의 최종 산출물이 결국 Spec이므로, Spec 구조가 먼저 안정돼야 대화가 무엇을 채워야 하는지 명확해진다.

**노드 도입 순서:** Phase 0~2는 `server` 노드 하나로 진행하고, Windows 노드는 Phase 2 이후 SSH 연결부터 붙인다. 파일럿(Astro_Manager)이 웹 서비스라 Windows나 GPU가 필수는 아니므로 서두를 필요가 없다.

### 11.1 Phase 0 파일럿: Astro_Manager 속도 개선

**현황 (2026-10-25 확인)**
- 위치 `~/Astro_Manager`, git 저장소 있음. 백엔드 FastAPI(`main.py` 1,760줄, 엔드포인트 약 60개), `satellite_tracker.py` 1,302줄(astropy, 영상 처리 기반 위성 궤적 검출 등), 프론트엔드 React+Vite(`App.jsx` 약 10,000줄).
- 데이터: SQLite `astro.db` 약 500KB (복사가 쉬움), 미디어 약 631GB(복사 불가), 썸네일 20MB.
- 운영 중인 서비스다 (컨테이너 `astro-manager`, 포트 35003).
- **자동 테스트가 없다.** → 속도만 보고 최적화하면 동작이 바뀌어도 알 수 없으므로, **정확성 gate를 먼저 만드는 것이 Phase 0의 첫 작업**이다.

**사용자가 체감하는 지연 지점 (2026-10-25 사용자 지정)**
1. FOV 시뮬레이터의 이미지 뷰어 (Aladin Lite)
2. Tonight's Setup
3. Tonight's Setup에서 방위 등 필터를 바꿀 때 목록이 갱신되는 속도

세 곳 모두 **백엔드가 아니라 브라우저 쪽(프론트엔드)** 문제다. 코드를 확인한 결과는 다음과 같다.

| 지점 | 코드상 구조 | 지연의 주된 원인 (추정) | RSI 적합도 |
|---|---|---|---|
| **Tonight's Setup 필터 갱신** | 약 800개 대상의 고도·방위를 브라우저에서 `astronomy-engine`으로 계산. 행 컴포넌트(`TonightPickRow`)가 렌더할 때마다 대상별 고도 곡선을 계산하고, 시간 격자마다 태양 고도까지 다시 계산 | 방위 필터를 바꾸면 `skyWindowMinutes` prop이 바뀌어 `React.memo`가 무력화되고 **보이는 모든 행이 곡선을 다시 계산**하는 것으로 보인다. 태양 고도는 모든 대상에 공통인데 행마다 반복 계산된다. 무거운 계산이 메인 스레드에서 돌고, 지연 업데이트(`useDeferredValue`)나 Web Worker는 쓰지 않는다 | ⭐⭐⭐ **가장 적합.** 계산이 결정적(같은 입력이면 같은 출력)이라 정확성 비교와 시간 측정이 모두 쉽다 |
| **Tonight's Setup 초기 표시** | 위와 같은 계산 + 목록 카드의 썸네일을 CDS `hips2fits`(프랑스 원격 서버)에서 받아옴 | 계산 부분은 위와 같고, 썸네일은 **외부 서버 응답 속도**에 좌우됨 | ⭐⭐ 계산 부분만 대상으로 하면 적합. 썸네일은 캐시나 프록시 같은 구조 변경으로 별도 처리 |
| **Aladin Lite** | `aladin.cds.unistra.fr/.../v3/latest/aladin.js`를 CDN에서 불러오고, 하늘 이미지(DSS2 컬러 타일)를 CDS 서버에서 받음 | 대부분 **외부 네트워크(스크립트·타일 다운로드)와 WebGL 렌더링**. 우리 코드로 줄일 수 있는 부분이 작다 | ⭐ RSI 루프로는 부적합. 측정값이 외부 서버 상태에 크게 흔들린다. 스크립트 미리 불러오기, 버전 고정, 더 가까운 미러 사용, 타일 캐시 프록시 등은 **사람이 한 번 설계해서 적용**하는 편이 낫다 |

**결론: 파일럿 = "Tonight's Setup 계산·필터 갱신 속도"**

**지표 설계 (초안)**
- **측정 방법 두 층**
  1. *계산 벤치마크 (매 반복):* 필터링·정렬·고도 곡선 계산 함수를 고정 입력(날짜·촬영지·장비·필터 조합 10여 개)으로 Node에서 실행해 소요 시간을 잰다. 빠르고 잡음이 적다.
  2. *실제 화면 측정 (후보 확정 시):* 헤드리스 브라우저(Playwright)로 테스트 인스턴스를 열고 방위 필터를 조작한 뒤, 목록 갱신이 끝날 때까지의 시간과 메인 스레드가 오래 막힌 구간(long task)을 잰다.
- **기준 시나리오 S1 "집 베란다" (사용자 실사용 조합)**

  | 항목 | 값 |
  |---|---|
  | 장비 | 망원경·카메라 모두 Seestar S30 Pro |
  | 카테고리 | 성운, 성단 |
  | 고도 | 0° ~ 55° |
  | 화각 채움 | 30% ~ 100% |
  | 촬영 난이도 | 0 ~ 50 |
  | 방위 | SW, S, SE만 |

  촬영지와 날짜·시각은 고정값으로 넣는다 (예: 기본 촬영지, 2026-11-15 21:00 KST).

- **측정할 조작 (S1 상태에서 시작)**

  | ID | 조작 | 의도 |
  |---|---|---|
  | S1-0 | S1 필터를 적용한 상태로 처음 표시 | 초기 계산 비용 |
  | S1-az | 방위 SE 끄기 → 다시 켜기 | **사용자가 지적한 지연 지점** |
  | S1-alt | 고도 최대 55° → 60° → 55° 드래그 | 슬라이더 연속 조작 |
  | S1-fit | 화각 채움 최소 30% → 50% | 범위 필터 |
  | S1-cat | 카테고리에서 성단 끄기 → 켜기 | 대상 풀 자체가 바뀌는 조작 |
  | W-az | 필터 없음 상태에서 방위 하나 지정 | 대상이 가장 많은 최악의 경우 |

  objective는 S1-az에 가중치를 가장 크게 두고(예: 50%), 나머지는 나눠서 합산한다. W-az는 "특수 케이스에만 최적화해서 일반 경우가 느려지는" 상황을 막는 감시 지표로 쓴다.

- **gate (정확성):** 고정 입력별로 현재 코드가 내놓는 **대상 목록·순서·표시 값**을 스냅샷으로 저장하고, 반복마다 완전히 같은지 비교한다.
- **objective:** 필터 변경 시나리오의 갱신 시간 중앙값 (최소화).
- **주의:** 계산 시각을 `new Date()`(현재 시각)로 쓰는 부분이 있다. 측정이 재현되려면 **시각을 고정 주입할 수 있게** 해야 하는데, 이건 루프 시작 전에 사람이 한 번 손보는 준비 작업이다.

**Phase 0 작업 순서**
1. **격리 환경:** git worktree + `astro.db` 복사본으로 테스트 인스턴스를 별도 포트(예: 35103)에 띄운다. 이 파일럿에는 미디어가 필요 없다. 운영 컨테이너와 `/data2`는 건드리지 않는다.
2. **측정 가능하게 만들기:** 현재 시각 주입 지점 정리, 계산 함수를 Node에서 불러 쓸 수 있도록 분리(동작 변경 없이). 사람이 검토해서 병합한다.
3. **정확성 gate:** 고정 입력별 결과 스냅샷 생성 → 사람이 한 번 검토해 확정.
4. **벤치마크 하네스:** 계산 벤치마크(Node) + 화면 측정(Playwright). 같은 코드를 5회 측정해 **잡음 폭을 먼저 재고**, 그보다 작은 개선은 개선으로 치지 않는다.
5. **Spec v1 수기 작성:** 수정 가능 경로 = `frontend/src/**`(스냅샷·벤치마크 제외), Judge = `human`.
6. **루프 실행:** 반복 수 상한 5회. `rate_limit_event`로 반복당 사용량(5시간 주기 % 증가분)을 기록한다. 이 값이 Pro 플랜 운용값의 근거가 된다.
7. **결과 반영:** 채택된 변경은 사람이 검토한 뒤 원래 저장소에 병합하고 운영 컨테이너를 다시 빌드한다. 루프 밖에서 수동으로 한다.

**이후 확장 후보:** Tonight's Setup 썸네일 캐시 → Aladin Lite 로딩 개선(사람 설계) → 백엔드 API 응답 속도.

---

## 12. 남은 결정사항

**확정됨**
- [x] 루프 본체는 홈서버, 실행 노드는 SSH로 여러 대 (server / win-gpu / rpi)
- [x] 구독 플랜은 현재 Pro → 절약 모드로 운용, 데이터가 쌓이면 업그레이드 판단
- [x] Phase 0 파일럿은 Astro_Manager 속도 개선

**남은 것**
- [x] 파일럿 범위: Tonight's Setup 계산·필터 갱신 속도 (Aladin Lite는 RSI 대상에서 제외하고 별도 처리)
- [x] 사용량 측정: CLI의 `rate_limit_event`와 statusline `rate_limits` 사용
- [x] 기준 필터 시나리오: S1 "집 베란다" (11.1절)
- [ ] **본인 사용과의 배분:** 평소 직접 Claude Code를 쓰는 시간대와, 루프가 쓸 수 있는 사용량 비중 상한
- [ ] **기본 Judge 정책:** `human`으로 시작해 신뢰가 쌓이면 `hybrid`로 전환하는 방식에 동의하는지
- [ ] **도메인 서브도메인 이름** (예: `rsi.<도메인>`)
- [ ] (나중에) Windows 노드의 OpenSSH Server 활성화와 LAN IP 고정

---

## 13. 리스크

| 리스크 | 영향 | 대응 |
|---|---|---|
| 사용량 한도로 루프가 자주 멈춤 | 높음 | 스케줄러, 모델 계층화, 컨텍스트 절약, 자동 재개 |
| 본인 작업용 사용량까지 소진 | 높음 | 루프 사용량 비중 상한, 시간대 예약 |
| 에이전트의 지표 조작 | 높음 | 보호 경로, 분리 세션 채점, 보류 세트 |
| 대화형 설계가 모호한 Spec을 만듦 | 중간 | 지표 검증 단계 통과 필수, 사람 최종 승인 |
| 범위 과대로 완성 지연 | 높음 | Phase별로 쓸모 있는 상태 확보, Phase 0부터 실사용 |
| 공개 도메인 노출 | 중간 | TOTP, 로그인 제한, 명령 화이트리스트 (임의 셸 금지) |
| Claude Code CLI 출력 형식·정책 변경 | 중간 | 엔진 어댑터로 파싱 로직 격리, 버전 고정 |
| 홈서버 메모리 부족 (에이전트 겸용) | 중간 | 동시 실행 1, setroubleshootd 정리, 모니터링 |
| 벤치마크 측정 잡음 (홈서버의 다른 서비스 부하) | 중간 | 반복 측정 중앙값, 잡음 폭 이하 개선은 무시, 부하 낮은 시간대 측정 |
| 운영 중인 서비스·데이터 손상 | 높음 | 운영 컨테이너·/data2 접근 금지, 복사본과 별도 포트에서만 실행 |
| Windows 노드 절전·GUI 불가 | 낮음 | 전원 설정·Wake-on-LAN, GUI 검증은 별도 방식 |

---

## 다음 액션
1. 홈서버에 Claude Code CLI 설치·로그인
2. (선택) setroubleshootd 정리로 메모리 확보
3. Phase 0 (11.1절): 격리 환경 → 측정 가능하게 정리 → 결과 스냅샷 → 벤치마크 하네스 → Spec v1 → 5회 루프 실행
4. Phase 0에서 확정된 이벤트 스키마와 Spec 구조를 기준으로 Phase 1 착수
