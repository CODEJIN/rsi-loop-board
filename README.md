# rsi-loop-board

Claude Code를 헤드리스로 돌려 코드를 반복 개선하는 **RSI(Recursive Self-Improvement) 루프**와, 그 루프를 휴대폰에서도 지켜보고 제어하는 **웹 대시보드**입니다.

> A self-hosted dashboard and orchestrator for running Claude Code improvement loops against a measurable objective, with mobile-first monitoring and control. UI is Korean-only for now.

## 무엇을 하나

1. **설계:** 대화형 설계 도우미와 함께 "무엇을 개선할지, 어떻게 잴지, 누가 판정할지"를 정해 **Spec**(YAML)과 평가 스크립트를 만듭니다. 대화가 진행되면 Spec이 보드에 GUI로 채워지고, 도우미가 git 초기화, 작업 공간 clone, 스크립트·Spec 저장, 측정 런까지 전용 도구로 직접 실행합니다.
2. **루프:** 반복마다 에이전트가 변경 하나를 만들고, 평가 스크립트가 지표와 정확성 검사(gate)를 냅니다. 잡음보다 크게 좋아지면 채택하고, 아니면 버립니다.
   - 분석(planner), 구현(implementer), 검토(reviewer) 역할을 나누고, 모델 계층(opus/sonnet/haiku)과 사용량 예산을 지정할 수 있습니다.
   - 판정 정책은 자동, 사람 승인, 혼합 중에서 고릅니다.
3. **실행 노드:** 평가를 SSH로 다른 머신(Windows GPU PC, 라즈베리파이 등)에서 돌릴 수 있습니다.
4. **긴 평가 대응:** 딥러닝처럼 평가가 오래 걸리는 과제를 위한 장치입니다.
   - 평가 단계(smoke → proxy → full)와 단계별 조기 탈락
   - 학습 곡선 진행 보고와 조기 종료
5. **대시보드:** 런 진행(단계 표시, 스파크라인, 학습 곡선), 일시정지·정지·추가 지시·승인, 사용량 링, 일정 예약, 감사 로그, 텔레그램 알림을 제공합니다. 로그인은 비밀번호와 TOTP입니다.

## 구조

```
dashboard/        FastAPI + SQLite 백엔드, React(Vite) 프론트엔드 — Docker 컨테이너 하나
  backend/app/    API, 이벤트 수집(ingest), 인증, 설계 대화, Spec 버전 관리
  frontend/src/   모바일 우선 UI
phase0/           호스트에서 도는 실행 계층
  orchestrator.py 대시보드 명령 큐를 읽어 루프를 시작·제어 (systemd 사용자 서비스)
  loop.py         Spec 하나로 개선 루프 실행, runs/<id>/events.jsonl 에 기록
  design_mcp.py   설계 도우미 전용 도구(MCP 서버)
  nodes.py        SSH 실행 노드
  evals/          평가 스크립트 (프로젝트별)
  specs/          처음 가져올 Spec 파일
docs/             설계 문서 (역할 분리, 긴 평가)
deploy/           systemd 서비스 예시
bench/            Playwright 기반 측정·화면 확인 스크립트
```

대시보드는 명령을 DB 큐에 넣기만 합니다. 실제 실행은 호스트의 오케스트레이터가 하고, 루프가 남긴 이벤트 파일을 대시보드가 읽어 실시간(SSE)으로 보여줍니다.

## 요구 사항

- Linux 호스트, Docker, Python 3.10+ (PyYAML)
- [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) — 구독 로그인 또는 API 키
- (선택) 외부 접속용 리버스 프록시 또는 터널, 텔레그램 봇

## 설치

```bash
# 1) 대시보드
cd dashboard
cp .env.example .env
python generate_hash.py '<비밀번호>'     # 출력값을 .env의 ADMIN_PASSWORD_HASH에 넣기
docker compose up -d --build            # 127.0.0.1:35004

# 2) 오케스트레이터 (호스트)
cp deploy/rsi-orchestrator.service.example ~/.config/systemd/user/rsi-orchestrator.service   # 경로 수정
systemctl --user daemon-reload && systemctl --user enable --now rsi-orchestrator
```

처음 로그인하면 TOTP 등록 화면이 나옵니다. 그다음 **설계** 탭에서 대상 저장소 경로를 넣고 대화를 시작하면 됩니다.

## Spec과 평가 계약

- 평가 명령은 `--repo <후보 경로> --out <결과 JSON> --label <이름>`을 받습니다.
- 결과 JSON 형식은 `{"objective": 숫자, "gates": [{"gate", "passed", "detail"}], "breakdown": {...}}`입니다.
- 단계 평가, 진행 보고, 조기 종료 같은 선택 기능은 [docs/LONG_EVAL_DESIGN.md](docs/LONG_EVAL_DESIGN.md)를 참고하세요.
- Spec의 모든 키는 [phase0/design_prompt.md](phase0/design_prompt.md)에 설명되어 있습니다.

## 현재 상태와 한계

개인 홈서버에서 쓰려고 만든 것이라 다음 한계가 있습니다.
- 일부 경로가 `/home/heejo/...`로 하드코딩되어 있습니다(설정으로 옮길 예정).
- 사용량 제어가 Claude 구독의 5시간·7일 사용률을 기준으로 짜여 있습니다. API 키(토큰 과금)로 쓰려면 비용 기준 예산이 필요합니다.
- 사용자는 한 명(관리자)만 지원하고, UI는 한국어만 있습니다.
- `phase0/evals/`, `phase0/specs/`에는 제 개인 프로젝트용 예시가 들어 있습니다.

## 라이선스

[MIT](LICENSE)
