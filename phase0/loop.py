#!/usr/bin/env python3
"""RSI Loop 실행기 — Spec(YAML) 하나로 개선 루프를 돌립니다.

반복 1회 (docs/ROLES_DESIGN.md §5):
  1) 체크포인트(일시정지·추가 지시·역할 변경) → 런 예산 확인 → 자동 조정기(auto)
  2) planner (런 시작·정체·채택 후) → 가설 후보 / 이번 반복에 가설 하나 배정
  3) implementer → 변경 1건 (변경 없음·범위 위반이면 기각)
  4) 평가 명령(JSON: objective, gates, breakdown) → gate 실패면 기각
  5) 채택 기준 이상 개선이면 reviewer(채택 후보 검사) → 판정 정책 → 채택 또는 사람 승인 대기
  6) 시도 기록(Spec 단위, 런을 넘어 누적)에 한 줄 추가
모든 과정은 runs/<run_id>/events.jsonl 에 기록하고, 대시보드가 이를 수집합니다.

실행 중 제어: 오케스트레이터가 runs/<run_id>/control.json 에 pause/resume/inject/stop_after/approvals/roles_override 를 쓰면
루프가 체크포인트에서 읽고 반영한 뒤 command_id 를 이벤트에 남깁니다.

사용: python loop.py --spec <spec.yaml> [--iterations N] [--run-id ID] [--command-id N] [--dry-run]
"""
import argparse
import copy
import fnmatch
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import yaml

import allocation as alloc_mod
import nodes as nodes_mod
import roles as roles_mod

ROOT = Path(__file__).resolve().parent
# 런 기록과 상태 폴더 — 검증용 실행이 실제 대시보드 기록에 섞이지 않도록 환경변수로 바꿀 수 있습니다.
RUNS_DIR = Path(os.getenv("RSI_RUNS_DIR", str(ROOT / "runs")))
STATE_DIR = Path(os.getenv("RSI_STATE_DIR", str(ROOT / "state")))   # Spec별 현재 best 측정값 캐시, 시도 기록
BEST_CACHE_HOURS = 24
SPEC = REPO = BEST = None
_agent_proc = None   # 실행 중인 역할 프로세스 — 루프가 중단되면 같이 정리합니다.

# 모든 Spec에 공통인 구현 에이전트 규칙. 프로젝트별 규칙은 Spec의 agent.instructions 에 둡니다.
PLATFORM_RULES = """\
- 이번 반복에서는 **가설 하나, 집중된 변경 하나**만 한다. 여러 아이디어를 한 번에 섞지 마라.
- 수정 가능 범위: {editable} 만. 다른 파일은 고치지 마라(자동 검사로 기각된다).
- 빌드·실행 도구는 없다. 코드를 읽고 편집만 한다. 문법 오류가 없도록 신중하게 편집하라.
- 끝나면 마지막 메시지로 다음 형식만 출력하라:
  SUMMARY: <한 줄 요약>
  HYPOTHESIS: <왜 지표가 좋아질 것으로 보는지 한두 문장>"""


def load_spec(path):
    global SPEC, REPO, BEST
    p = Path(path)
    SPEC = yaml.safe_load((p if p.is_absolute() else ROOT / p).read_text())
    REPO = Path(SPEC["workspace"]["repo"])
    BEST = SPEC["workspace"]["best_branch"]


class Run:
    def __init__(self, run_id=None):
        self.id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
        self.dir = RUNS_DIR / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.seq = 0
        self.last_rate_limit = None
        self.rate_resets = {}          # 창 → 재개 시각(epoch 초)
        self.consumed_injections = set()
        self.applied_role_commands = set()
        self.meter = alloc_mod.UsageMeter()
        self.role_wtok = {}            # 역할 → 가중 토큰 합 (런 종료 시 role_usage 계산)
        (self.dir / "loop.pid").write_text(str(os.getpid()))

    def control(self):
        path = self.dir / "control.json"
        try:
            return json.loads(path.read_text()) if path.exists() else {}
        except json.JSONDecodeError:
            return {}

    def event(self, type_, **data):
        self.seq += 1
        rec = {"seq": self.seq, "ts": datetime.now().isoformat(timespec="seconds"), "run": self.id, "type": type_, **data}
        with open(self.dir / "events.jsonl", "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[{rec['ts']}] {type_} {json.dumps(data, ensure_ascii=False)[:300]}", flush=True)


def sh(cmd, cwd=None, check=True, timeout=None):
    p = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {cmd}\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}")
    return p


def git(*args, check=True):
    return sh(["git", *args], cwd=REPO, check=check).stdout.strip()


# ---------------------------------------------------------------- 지표

def metric():
    m = SPEC["metric"]
    return {"name": m["name"], "unit": m.get("unit", ""), "direction": m.get("direction", "minimize")}


def improvement(best_value, value):
    """좋아진 양(양수 = 개선). 지표 방향(minimize/maximize)에 따라 부호를 맞춥니다."""
    return (best_value - value) if metric()["direction"] == "minimize" else (value - best_value)


def fmt_value(v):
    unit = metric()["unit"]
    return f"{v}{unit}" if v is not None else "—"


# ---------------------------------------------------------------- 시도 기록 (규칙 기반, 런을 넘어 누적)

def attempts_path():
    return STATE_DIR / "attempts" / f"{SPEC['spec']}.jsonl"


def load_attempts(n):
    p = attempts_path()
    if not p.exists():
        return []
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return rows[-n:]


def record_attempt(run, rec, best_before, result):
    """가설·결과와 함께, 측정 세부에서 가장 크게 변한 항목 3개를 남겨 '무엇이 효과 있었는지'를 다음 반복이 알게 합니다."""
    top = {}
    if result and best_before:
        b, c = best_before.get("breakdown") or {}, result.get("breakdown") or {}
        deltas = {k: round(c[k] - b[k], 3) for k in c if isinstance(c.get(k), (int, float)) and isinstance(b.get(k), (int, float))}
        top = dict(sorted(deltas.items(), key=lambda kv: -abs(kv[1]))[:3])
    row = {"run": run.id, "iteration": rec["iteration"], "hypothesis": rec.get("assigned") or rec.get("hypothesis"),
           "summary": rec.get("summary"), "decision": rec.get("decision"), "gain": rec.get("gain"),
           "failed_gate": rec.get("failed_gate"), "stage_reached": rec.get("stage_reached"), "review": rec.get("review"), "top_changes": top,
           "ts": datetime.now().isoformat(timespec="seconds")}
    attempts_path().parent.mkdir(parents=True, exist_ok=True)
    with open(attempts_path(), "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 역할 실행

def run_role(run, iteration, name, prompt, roles, tools):
    """역할 하나를 독립 Claude Code 세션으로 실행합니다. 모델 오류면 기본 모델로 한 번 다시 실행합니다.
    반환: (최종 텍스트, result 메시지 또는 None, timed_out)"""
    global _agent_proc
    cfg = roles[name]
    model = alloc_mod.model_for(SPEC, roles, name)
    for attempt in (1, 2):
        run.event("role_start", iteration=iteration, role=name, tier=cfg.get("tier"), model=model or "default")
        util_before = (run.last_rate_limit or {}).get("five_hour")
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--tools", tools,
               "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]
        if name == "implementer":
            cmd += ["--permission-mode", "acceptEdits"]
        if model:
            cmd += ["--model", model]
        raw = open(run.dir / f"iter-{iteration}.{name}{'' if attempt == 1 else '.retry'}.jsonl", "w")
        proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        _agent_proc = proc
        timer = threading.Timer(cfg.get("timeout_min", 20) * 60, proc.kill)
        timer.start()
        result, texts, tool_calls, other = None, [], 0, []
        for line in proc.stdout:
            raw.write(line)
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                other.append(line.strip())
                continue
            t = msg.get("type")
            if t == "rate_limit_event":
                info = msg.get("rate_limit_info", {})
                windows = info.get("unifiedWindows") or {}
                run.last_rate_limit = {k: v.get("utilization") for k, v in windows.items()}
                run.rate_resets = {k: v.get("resetsAt") for k, v in windows.items() if v.get("resetsAt")}
                run.meter.observe(run.last_rate_limit.get("five_hour"), run.rate_resets.get("five_hour"))
                run.event("usage", iteration=iteration, role=name, windows=run.last_rate_limit, resets=run.rate_resets,
                          status=info.get("status"))
            elif t == "assistant":
                for block in msg.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        tool_calls += 1
                        inp = block.get("input", {})
                        run.event("agent_tool", iteration=iteration, role=name, tool=block.get("name"),
                                  target=inp.get("file_path") or inp.get("pattern") or "")
                    elif block.get("type") == "text":
                        texts.append(block.get("text", ""))
            elif t == "result":
                result = msg
        proc.wait()
        timer.cancel()
        raw.close()
        _agent_proc = None
        timed_out = result is None and proc.returncode not in (0, None)
        failed = result is None or result.get("is_error")
        usage = (result or {}).get("usage") or {}
        # 기본 모델로 대체 실행했다면 계층 가중치도 표준으로 봅니다(지정 계층 모델로 돌지 않았으므로).
        wtok = alloc_mod.weighted_tokens(usage, cfg.get("tier") if model else "standard", SPEC.get("tier_factor"))
        run.role_wtok[name] = run.role_wtok.get(name, 0) + wtok
        error_text = ((result or {}).get("result") or " ".join(other))[-300:] if failed else None
        run.event("role_done", iteration=iteration, role=name, model=model or "default", ok=not failed, error=error_text,
                  duration_ms=(result or {}).get("duration_ms"), tool_calls=tool_calls, wtok=wtok,
                  usage={k: usage.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                       "cache_read_input_tokens", "output_tokens")},
                  util_before=util_before, util_after=(run.last_rate_limit or {}).get("five_hour"))
        # 지정 모델을 쓸 수 없어 실패한 경우만 기본 모델로 재시도합니다(시간 초과·일반 오류는 재시도하지 않음).
        if failed and model and not timed_out and attempt == 1 and re.search(r"model", error_text or "", re.I):
            run.event("model_fallback", iteration=iteration, role=name, model=model, error=error_text)
            model = None
            continue
        final = (result or {}).get("result") or "\n".join(texts)
        return final, result, timed_out
    return "", None, False


def build_prompt(iteration, best, attempts_txt, injections=(), assigned=None):
    agent, target = SPEC["agent"], SPEC["target"]
    m = metric()
    better = "낮을수록" if m["direction"] == "minimize" else "높을수록"
    details = best.get("details") or {}
    detail_txt = "\n".join(f"  - {k}: {v}" for k, v in (details.get("slowest_steps") or {}).items())
    extra = ""
    if injections:
        # 사용자가 대시보드에서 보낸 추가 지시 — 규칙(범위·결과 불변)보다 우선하지는 않습니다.
        extra = "## 사용자 추가 지시 (이번 반복에 반영할 것, 단 위 규칙은 그대로 지킨다)\n" + \
            "\n".join(f"- {i['text']}" for i in injections) + "\n\n"
    if assigned:
        extra += (f"## 이번 반복에 배정된 가설 (분석 역할 제안)\n- {assigned['title']}\n- 근거: {assigned.get('rationale', '')}\n"
                  f"- 관련 파일: {', '.join(assigned.get('files') or [])}\n이 가설을 구현하라. 코드를 읽어 보니 성립하지 않으면, "
                  "가장 가까운 대안 하나를 구현하고 SUMMARY에 그 이유를 적어라.\n\n")
    rules = PLATFORM_RULES.format(editable=", ".join(target["editable_paths"]))
    stages = SPEC["evaluation"].get("stages")
    stage_txt = ("\n- 평가 단계: " + " → ".join(
        f"{st.get('name')}({fmt_value((best.get('stages') or {}).get(st.get('name'), {}).get('objective'))}"
        + (f", best보다 {st['promote_within_pct']}% 넘게 나쁘면 탈락" if st.get('promote_within_pct') is not None else "") + ")"
        for st in stages) + " — 앞 단계에서 떨어지면 뒤 단계는 돌지 않는다") if stages else ""
    return f"""너는 RSI Loop의 개선 에이전트(구현 역할)다. 작업 디렉터리는 {SPEC.get('project', '대상')} 저장소의 격리된 복사본이다.

목표: {target['goal']}

## 규칙
{rules}
{agent.get('instructions', '').rstrip()}

## 현재 최고 기록 (반복 {iteration} 시작 시점)
- {m['name']}: {fmt_value(best.get('objective'))} ({better} 좋음)
- 세부: {json.dumps(best.get('breakdown') or {}, ensure_ascii=False)}
{detail_txt}{stage_txt}

## 측정 방법
{SPEC['evaluation'].get('description', '').rstrip()}

## 배경 · 사전 분석
{agent.get('context', '(없음)').rstrip()}

## 지금까지의 시도 (런을 넘어 누적, 같은 방향을 반복하지 마라)
{attempts_txt}

{extra}{agent.get('focus') or '코드를 읽고, 가장 효과가 클 것으로 보이는 변경 하나를 적용하라.'}
"""


# ---------------------------------------------------------------- 평가

def changed_files():
    # git()은 출력 앞뒤 공백을 지우므로 porcelain 형식(" M path")을 자르면 첫 글자가 사라집니다.
    modified = git("diff", "--name-only", "HEAD").splitlines()
    untracked = git("ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({f for f in modified + untracked if f})


def out_of_scope(files):
    allowed = SPEC["target"]["editable_paths"]
    return [f for f in files if not any(fnmatch.fnmatch(f, pat) for pat in allowed)]


def resolve_node(ref):
    """노드 지정 해석: 'server', 'win-gpu' 같은 ID 또는 'tag:gpu'(그 태그를 가진 첫 노드). 노드 정보는 Spec 스냅샷의 nodes에 있습니다."""
    nodes = SPEC.get("nodes") or {}
    if not ref or ref in ("server", "local"):
        return "server", {"transport": "local"}
    if ref.startswith("tag:"):
        tag = ref[4:]
        for nid, n in nodes.items():
            if tag in (n.get("tags") or []):
                return nid, n
        raise RuntimeError(f"태그 '{tag}'를 가진 노드가 없습니다")
    if ref not in nodes:
        raise RuntimeError(f"노드 '{ref}'가 없거나 꺼져 있습니다")
    return ref, nodes[ref]


# ---------------------------------------------------------------- 평가: 단계 · 진행 보고 · 조기 종료 (docs/LONG_EVAL_DESIGN.md)

PROGRESS_EVENT_SEC = 20     # 진행 이벤트 간격 — 학습 곡선은 촘촘할 필요가 없음
REMOTE_POLL_SEC = int(os.getenv("RSI_REMOTE_POLL_SEC", "20"))   # 원격 진행 파일 확인 간격
_eval_proc = None           # 실행 중인 로컬 평가 프로세스 — 루프가 중단되면 같이 정리합니다.


def eval_stages():
    return SPEC["evaluation"].get("stages") or [{"name": "main"}]


def _downsample(curve, n=120):
    if len(curve) <= n:
        return curve
    step = len(curve) / n
    return [curve[int(i * step)] for i in range(n)] + [curve[-1]]


def _better_or_close(value, ref, pct):
    """value가 ref보다 pct% 넘게 나쁘지 않으면 True (지표 방향 기준)."""
    margin = abs(ref) * pct / 100
    return value <= ref + margin if metric()["direction"] == "minimize" else value >= ref - margin


class Progress:
    """평가 스크립트가 --progress 파일에 쓰는 {"step","total","value"} 줄을 모으고, best 곡선과 비교해 조기 종료를 판단합니다."""

    def __init__(self, run, iteration, stage, rule, best_curve):
        self.run, self.iteration, self.stage = run, iteration, stage
        self.rule = rule or {}
        self.best = sorted(best_curve or [], key=lambda p: p["step"])
        self.curve, self.bad, self.last_event = [], 0, 0.0

    def feed_lines(self, text):
        """파일 전체 또는 끝부분 텍스트를 받아 새 지점만 반영합니다. 조기 종료 사유(문자열)나 None을 돌려줍니다."""
        last = self.curve[-1]["step"] if self.curve else None
        reason = None
        for line in text.splitlines():
            try:
                p = json.loads(line)
                p = {"step": float(p["step"]), "value": None if p.get("value") is None else float(p["value"]),
                     **({"total": float(p["total"])} if p.get("total") else {})}
            except (ValueError, KeyError, TypeError):
                continue
            if last is not None and p["step"] <= last:
                continue
            self.curve.append(p)
            last = p["step"]
            reason = reason or self._check(p)
        if self.curve and time.time() - self.last_event > PROGRESS_EVENT_SEC:
            self.last_event = time.time()
            p = self.curve[-1]
            self.run.event("eval_progress", iteration=self.iteration, stage=self.stage, step=p["step"],
                           total=p.get("total"), value=p.get("value"), points=len(self.curve))
        return reason

    def _check(self, p):
        r = self.rule
        if not r or not self.best or p.get("value") is None:
            return None
        total = p.get("total") or self.best[-1]["step"]
        if "after_step" in r and "after_pct" not in r:
            if p["step"] < r["after_step"]:
                return None
        elif p["step"] < total * r.get("after_pct", 30) / 100:
            return None
        ref = next((b for b in reversed(self.best) if b["step"] <= p["step"] and b.get("value") is not None), None)
        if ref is None:
            return None
        margin = abs(ref["value"]) * r.get("worse_pct", 10) / 100
        worse = p["value"] > ref["value"] + margin if r.get("direction", "minimize") == "minimize" else p["value"] < ref["value"] - margin
        self.bad = self.bad + 1 if worse else 0
        if self.bad >= r.get("patience", 2):
            return (f"조기 종료: step {p['step']:g}에서 {p['value']:.4g} — 같은 지점 best {ref['value']:.4g}보다 "
                    f"{r.get('worse_pct', 10)}% 넘게 나쁨 ({self.bad}회 연속)")
        return None


def _fail(gate, detail):
    return {"objective": None, "gates": [{"gate": gate, "passed": False, "detail": str(detail)[-800:]}], "breakdown": {}}


def _stage_args(stage, progress_path):
    return shlex.split(stage.get("args") or "") + (["--progress", str(progress_path)] if progress_path else [])


def _run_local_stage(run, label, stage, tag, prog):
    """로컬 평가 한 단계. 진행 파일을 2초마다 읽어 조기 종료를 판단합니다."""
    global _eval_proc
    ev = SPEC["evaluation"]
    out = run.dir / f"eval-{label}{'' if stage['name'] == 'main' else '-' + stage['name']}.json"
    pfile = run.dir / f"progress-{label}-{stage['name']}.jsonl" if ev.get("progress") else None
    for f in (out, pfile):
        if f and f.exists():
            f.unlink()
    cmd = shlex.split(ev["command"]) + _stage_args(stage, pfile) + ["--repo", str(REPO), "--out", str(out), "--label", tag]
    log = open(run.dir / f"eval-{label}-{stage['name']}.log", "w")
    _eval_proc = proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    deadline = time.time() + stage.get("timeout_min", ev.get("timeout_min", 30)) * 60
    stop = None
    try:
        while proc.poll() is None:
            time.sleep(2)
            if pfile and pfile.exists():
                stop = prog.feed_lines(pfile.read_text(errors="replace"))
            if stop or time.time() > deadline:
                stop = stop or "평가 시간 초과"
                _kill_group(proc)
                break
        if pfile and pfile.exists():
            prog.feed_lines(pfile.read_text(errors="replace"))
    finally:
        log.close()
        _eval_proc = None
    if stop:
        return _fail("early_stop" if stop.startswith("조기") else "eval", stop), bool(stop.startswith("조기"))
    if proc.returncode == 0 and out.exists():
        return json.loads(out.read_text()), False
    tail = (run.dir / f"eval-{label}-{stage['name']}.log").read_text(errors="replace")[-800:]
    return _fail("eval", tail), False


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=15)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _kill_remote(node, tag):
    # 패턴 첫 글자를 [x]로 감싸 kill 명령을 실행하는 셸 자신이 걸리지 않게 합니다.
    if node.get("os") == "windows":
        cmd = (f"Get-CimInstance Win32_Process | Where-Object {{ $_.ProcessId -ne $PID -and $_.CommandLine -like '*{tag}*' }} | "
               f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}")
    else:
        cmd = f"pkill -f -- '[{tag[0]}]{tag[1:]}' ; true"
    try:
        nodes_mod.run(node, cmd, timeout=30)
    except Exception:
        pass


def _run_remote_stage(run, label, stage, tag, prog, node_id, node, dest, cmd):
    """원격 노드 평가 한 단계. 본 실행은 별도 스레드의 SSH로, 진행 파일은 주기적으로 끝부분만 읽습니다."""
    ev = SPEC["evaluation"]
    out, pfile = f"{dest}/rsi-eval.json", (f"{dest}/rsi-progress.jsonl" if ev.get("progress") else None)
    # 진행 파일 경로는 dest처럼 따옴표 없이 넘깁니다(리눅스 노드의 ~/ 경로가 펼쳐지도록).
    args = " ".join(shlex.quote(a) for a in _stage_args(stage, None)) + (f" --progress {pfile}" if pfile else "")
    win = node.get("os") == "windows"
    if win:
        script = (f"Set-Location '{dest}'; Remove-Item -ErrorAction SilentlyContinue '{out}'{f', {chr(39)}{pfile}{chr(39)}' if pfile else ''}; "
                  f"{cmd} {args} --repo '{dest}' --out '{out}' --label '{tag}'; Get-Content -Raw '{out}'")
    else:
        script = f"cd {dest} && rm -f {out} {pfile or ''} && {cmd} {args} --repo {dest} --out {out} --label {tag} && cat {out}"
    timeout = stage.get("timeout_min", ev.get("timeout_min", 30)) * 60
    box = {}

    def main_run():
        try:
            box["r"] = nodes_mod.run(node, script, timeout=timeout + 60)
        except Exception as e:      # SSH 실패·시간 초과 — 결과 없음으로 처리
            box["e"] = str(e)
    th = threading.Thread(target=main_run, daemon=True)
    th.start()
    deadline, stop = time.time() + timeout, None
    tail = (f"Get-Content -Tail 300 '{pfile}' -ErrorAction SilentlyContinue" if win else f"tail -n 300 {pfile} 2>/dev/null; true") if pfile else None
    while th.is_alive():
        th.join(REMOTE_POLL_SEC if pfile else 5)
        if pfile and th.is_alive():
            try:
                stop = prog.feed_lines(nodes_mod.run(node, tail, timeout=60)[1])
            except Exception:
                pass
        if th.is_alive() and (stop or time.time() > deadline):
            stop = stop or "평가 시간 초과"
            _kill_remote(node, tag)
            th.join(30)
            break
    if stop:
        return _fail("early_stop" if stop.startswith("조기") else f"eval@{node_id}", stop), stop.startswith("조기")
    if pfile:       # 마지막 몇 지점은 폴링 사이에 끝났을 수 있어 한 번 더 읽습니다
        try:
            prog.feed_lines(nodes_mod.run(node, tail, timeout=60)[1])
        except Exception:
            pass
    if "r" not in box:
        return _fail(f"eval@{node_id}", box.get("e", "원격 실행 결과 없음")), False
    rc, stdout, stderr = box["r"]
    if rc != 0 or "{" not in stdout:
        return _fail(f"eval@{node_id}", stderr or stdout), False
    return json.loads(stdout[stdout.index("{"):]), False


def _matrix_gates(run, label):
    """evaluation.matrix: 다른 노드에서도 통과해야 하는 검사(예: Windows에서 테스트). 종료 코드 0이면 통과."""
    gates = []
    for entry in SPEC["evaluation"].get("matrix") or []:
        name = entry.get("name") or "check"
        try:
            node_id, node = resolve_node(entry["node"])
            dest = nodes_mod.sync(node, REPO, "HEAD", f"{SPEC['spec']}")
            prefix = f"Set-Location '{dest}'; " if node.get("os") == "windows" else f"cd {dest} && "
            rc, out, err = nodes_mod.run(node, prefix + entry["command"], timeout=entry.get("timeout_min", 20) * 60)
            gates.append({"gate": f"{name}@{node_id}", "passed": rc == 0, "detail": None if rc == 0 else (err or out)[-800:]})
        except Exception as e:
            gates.append({"gate": f"{name}@{entry.get('node')}", "passed": False, "detail": f"노드 실행 실패: {str(e)[:300]}"})
    return gates


def evaluate(run, label, best=None, iteration=None):
    """Spec의 평가를 단계별로 실행합니다(evaluation.stages가 없으면 한 단계). best를 주면 후보 평가로 보고
    단계 승급 조건(promote_within_pct)과 학습 곡선 조기 종료(early_stop)를 적용합니다. best를 잴 때는 모든 단계를 끝까지 돌립니다.
    반환: 마지막으로 실행한 단계의 결과 + 누적 gate + 단계별 값(stages)·곡선. evaluation.matrix는 모든 단계를 통과했을 때만."""
    ev = SPEC["evaluation"]
    stages, multi = eval_stages(), bool(ev.get("stages"))
    try:
        node_id, node = resolve_node(ev.get("node"))
        remote = node.get("transport") == "ssh"
        dest = nodes_mod.sync(node, REPO, "HEAD", f"{SPEC['spec']}") if remote else None
        remote_cmd = ev.get("remote_command") or ev["command"]
        m = re.search(r"evals/([\w-]+)/", remote_cmd)
        if remote and m and (ROOT / "evals" / m.group(1)).is_dir():
            # phase0/evals/<폴더>는 대상 저장소 밖이라 따로 보내고, 명령의 경로를 노드 쪽 위치로 바꿉니다.
            nodes_mod.send_dir(node, ROOT / "evals" / m.group(1), f"{dest}/.rsi-evals/{m.group(1)}")
            remote_cmd = remote_cmd.replace(f"evals/{m.group(1)}/", f".rsi-evals/{m.group(1)}/")
    except Exception as e:
        return _fail(f"eval@{ev.get('node') or 'local'}", f"노드 준비 실패: {e}")
    gates, stage_res, result, name = [], {}, None, None
    for idx, st in enumerate(stages):
        st = {**st, "name": st.get("name") or f"stage{idx + 1}"}
        name = st["name"]
        best_stage = ((best or {}).get("stages") or {}).get(name) or {}
        rule = (st.get("early_stop") or ev.get("early_stop") or {}) if best else None
        prog = Progress(run, iteration, name, rule, best_stage.get("curve"))
        tag = f"{run.id}-{label}-{name}"
        t0 = time.time()
        if remote:
            res, early = _run_remote_stage(run, label, st, tag, prog, node_id, node, dest, remote_cmd)
        else:
            res, early = _run_local_stage(run, label, st, tag, prog)
        st_gates = [{**g, "gate": f"{name}:{g['gate']}"} if multi else g for g in res.get("gates", [])]
        passed = all(g["passed"] for g in st_gates) and res.get("objective") is not None
        last = idx == len(stages) - 1
        pct = st.get("promote_within_pct")
        if passed and best and not last and pct is not None and best_stage.get("objective") is not None \
                and not _better_or_close(res["objective"], best_stage["objective"], pct):
            st_gates.append({"gate": f"promote:{name}", "passed": False,
                             "detail": f"{name} 단계 {fmt_value(res['objective'])} — 같은 단계 best {fmt_value(best_stage['objective'])}보다 "
                                       f"{pct}% 넘게 나빠 다음 단계로 가지 않음"})
            passed = False
        gates += st_gates
        stage_res[name] = {"objective": res.get("objective"), "curve": _downsample(prog.curve), "seconds": round(time.time() - t0)}
        if multi or early or prog.curve:
            run.event("stage_result", iteration=iteration, label=label, stage=name, index=idx, of=len(stages),
                      names=[x.get("name") or f"stage{i + 1}" for i, x in enumerate(stages)],
                      objective=res.get("objective"), passed=passed, early_stopped=early, seconds=round(time.time() - t0),
                      curve=_downsample(prog.curve, 60), best_curve=_downsample(best_stage.get("curve") or [], 60),
                      best_objective=best_stage.get("objective"))
        result = res
        if not passed:
            break
    final = {**result, "gates": gates, "stages": stage_res, "stage_reached": name}
    if ev.get("matrix") and all(g["passed"] for g in gates) and final.get("objective") is not None:
        final["gates"] = gates + _matrix_gates(run, label)
    return final


def cleanup_eval():
    cmd = SPEC["evaluation"].get("cleanup_command")
    if cmd:
        sh(shlex.split(cmd), cwd=ROOT, check=False)


def eval_key():
    """평가 설정이 바뀌면 이전 best 측정값을 재사용하지 않도록, 평가·지표 설정의 해시를 캐시 키에 넣습니다."""
    blob = json.dumps({"evaluation": SPEC["evaluation"], "metric": SPEC["metric"]}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


# ---------------------------------------------------------------- 제어 체크포인트

def checkpoint(run, iteration, where):
    """일시정지 요청이 있으면 해제될 때까지 기다립니다. 즉시 중단(SIGTERM)은 언제든 KeyboardInterrupt로 들어옵니다."""
    ctl = run.control()
    if not ctl.get("pause"):
        return
    run.event("paused", iteration=iteration, reason=f"사용자 요청 ({where})", command_id=ctl.get("pause_command_id"))
    while run.control().get("pause"):
        time.sleep(2)
    run.event("resumed", iteration=iteration, command_id=run.control().get("resume_command_id"))


def take_injections(run):
    pending = [i for i in run.control().get("injections", []) if i["command_id"] not in run.consumed_injections]
    run.consumed_injections.update(i["command_id"] for i in pending)
    return pending


def apply_role_override(run, iteration, roles, auto):
    """대시보드의 실행 중 역할 변경(set_roles) — 반복 경계에서만 반영합니다."""
    ov = run.control().get("roles_override")
    if not ov or ov.get("command_id") in run.applied_role_commands:
        return
    run.applied_role_commands.add(ov.get("command_id"))
    changes = []
    for name, patch in (ov.get("roles") or {}).items():
        if name not in roles and name in alloc_mod.DEFAULT_ROLES:
            roles[name] = dict(alloc_mod.DEFAULT_ROLES[name], enabled=False)
        if name not in roles or name == "implementer" and "enabled" in patch:
            continue
        for k in ("enabled", "when", "tier"):
            if k in patch and roles[name].get(k) != patch[k]:
                changes.append({"role": name, k: [roles[name].get(k), patch[k]]})
                roles[name][k] = patch[k]
    if auto:
        auto.set_user_roles(roles)
    run.event("roles_changed", iteration=iteration, by="user", changes=changes, reason="대시보드에서 변경",
              command_id=ov.get("command_id"))


def wait_for_approval(run, iteration, info):
    """사람 판정이 필요한 후보 — 대시보드에서 승인/거절할 때까지 기다립니다."""
    run.event("approval_required", iteration=iteration, **info)
    while True:
        decision = run.control().get("approvals", {}).get(str(iteration))
        if decision:
            run.event("approval", iteration=iteration, approved=decision["approved"], command_id=decision.get("command_id"))
            return decision["approved"]
        time.sleep(2)


def needs_human(gain, best_value, diff_lines):
    judge = SPEC.get("judge", {})
    policy = judge.get("policy", "auto")
    if policy == "human":
        return "정책: 모든 채택 후보는 사람 승인"
    if policy == "hybrid":
        h = judge.get("hybrid", {})
        pct = (gain / abs(best_value) * 100) if best_value else 0
        if h.get("improvement_pct_gte") is not None and pct >= h["improvement_pct_gte"]:
            return f"개선폭 {pct:.0f}% ≥ {h['improvement_pct_gte']}% (지나치게 큰 개선은 사람 확인)"
        if h.get("diff_lines_gte") is not None and diff_lines >= h["diff_lines_gte"]:
            return f"변경 {diff_lines}줄 ≥ {h['diff_lines_gte']}줄"
    return None


def wait_for_usage(run, why):
    """사용량 한도에 가까우면 해당 창의 재개 시각까지 기다렸다가 이어갑니다(재개 시각을 모르면 30분)."""
    resets = [t for t in run.rate_resets.values() if t and t > time.time()]
    resume_at = max(resets) + 60 if resets else time.time() + 1800
    run.event("paused", reason=f"사용량 한도: {why}", resume_at=datetime.fromtimestamp(resume_at).isoformat(timespec="seconds"))
    while time.time() < resume_at:
        if run.control().get("stop_after"):
            return False
        time.sleep(10)
    run.last_rate_limit = None   # 다음 역할 실행에서 새 값을 받습니다
    run.event("resumed", reason="사용량 창 재개 시각 도달")
    return True


def usage_ok(run):
    lim = run.last_rate_limit or {}
    b = SPEC["budget"]
    if (lim.get("five_hour") or 0) >= b.get("stop_if_five_hour_utilization_gte", 0.8):
        return False, f"five_hour {lim['five_hour']:.0%}"
    if (lim.get("seven_day") or 0) >= b.get("stop_if_seven_day_utilization_gte", 0.7):
        return False, f"seven_day {lim['seven_day']:.0%}"
    return True, ""


# ---------------------------------------------------------------- best 기준값

def establish_best(run):
    """현재 best 브랜치를 기준으로 삼습니다. 같은 커밋·같은 평가 설정의 최근 측정값이 있으면 재사용하고, 없으면 측정합니다."""
    ref = git("rev-parse", "--short", BEST)
    cache = STATE_DIR / f"{SPEC['spec']}.json"
    if cache.exists():
        st = json.loads(cache.read_text())
        age_h = (datetime.now() - datetime.fromisoformat(st["measured_at"])).total_seconds() / 3600
        if st["best_ref"] == ref and st.get("eval_key") == eval_key() and age_h < BEST_CACHE_HOURS:
            run.event("baseline_measured", source="cache", best_ref=ref, objective=st["result"]["objective"])
            return st["result"]
    git("checkout", "-q", BEST)
    result = evaluate(run, "best")
    failed = [g for g in result["gates"] if not g["passed"]]
    if failed or result.get("objective") is None:
        raise RuntimeError(f"현재 best 평가 실패: {failed[0]['gate'] if failed else 'objective 없음'} — {str(failed[0].get('detail'))[:300] if failed else ''}")
    save_best(ref, result)
    run.event("baseline_measured", source="measured", best_ref=ref, objective=result["objective"],
              stages={k: v.get("objective") for k, v in (result.get("stages") or {}).items()})
    return result


def save_best(ref, result):
    STATE_DIR.mkdir(exist_ok=True)
    (STATE_DIR / f"{SPEC['spec']}.json").write_text(json.dumps(
        {"best_ref": ref, "eval_key": eval_key(), "measured_at": datetime.now().isoformat(timespec="seconds"), "result": result},
        ensure_ascii=False))


# ---------------------------------------------------------------- 메인 루프

def main():
    # SIGTERM(kill)도 Ctrl+C와 같이 처리해서, 중단돼도 run_aborted 이벤트가 남고 작업 공간이 정리되게 합니다.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="Spec YAML 경로 (phase0/ 기준 상대경로 또는 절대경로)")
    ap.add_argument("--iterations", type=int)
    ap.add_argument("--dry-run", action="store_true", help="에이전트 없이 현재 best를 반복 측정만 함(잡음 확인용, 사용량 없음)")
    ap.add_argument("--run-id", help="오케스트레이터가 정한 런 ID")
    ap.add_argument("--command-id", type=int, help="이 런을 시작한 대시보드 명령 번호")
    a = ap.parse_args()
    load_spec(a.spec)
    iterations = a.iterations or SPEC["budget"].get("max_iterations", 5)
    noise = SPEC["metric"].get("noise_threshold")
    if noise is None and not a.dry_run:
        sys.exit("Spec의 metric.noise_threshold 를 먼저 정하세요 (dry-run으로 잡음을 측정할 수 있습니다)")

    run = Run(a.run_id)
    roles = alloc_mod.resolve_roles(SPEC)
    allocation = alloc_mod.resolve_allocation(SPEC)
    if git("rev-parse", "--verify", "--quiet", BEST, check=False) == "":
        git("branch", BEST, SPEC["workspace"]["base_ref"])
    git("checkout", "-q", BEST)
    # 기준값은 아래 establish_best가 baseline_measured 이벤트로 알립니다(측정에 수 분 걸릴 수 있어 시작을 먼저 알림).
    run.event("run_start", spec=SPEC["spec"], version=SPEC["version"], title=SPEC.get("title"), repo=str(REPO),
              best_ref=git("rev-parse", "--short", BEST), metric=metric(), noise_threshold=noise,
              judge=SPEC.get("judge", {}).get("policy", "auto"), iterations=iterations, dry_run=a.dry_run,
              roles=roles, allocation=allocation, command_id=a.command_id)

    history = []
    try:
        best = establish_best(run)
        _iterate(run, a.dry_run, iterations, best, noise or 0, history, roles, allocation)
    except KeyboardInterrupt:
        if _agent_proc and _agent_proc.poll() is None:
            _agent_proc.kill()
        if _eval_proc and _eval_proc.poll() is None:
            _kill_group(_eval_proc)
        git("checkout", "-q", "--", ".", check=False)
        git("checkout", "-q", BEST, check=False)
        stop_cid = run.control().get("stop_command_id")
        run.event("run_aborted", reason="대시보드에서 즉시 중단" if stop_cid else "중단 신호(SIGTERM/SIGINT)",
                  decisions=[h["decision"] for h in history], command_id=stop_cid)
        emit_role_usage(run)
        cleanup_eval()
        sys.exit(130)
    except Exception as e:
        git("checkout", "-q", "--", ".", check=False)
        git("checkout", "-q", BEST, check=False)
        run.event("run_aborted", reason=f"오류: {str(e)[:300]}", decisions=[h["decision"] for h in history])
        emit_role_usage(run)
        cleanup_eval()
        raise


def emit_role_usage(run):
    """역할별 사용량 추정 = 런 동안 늘어난 5시간 창 사용률(양수 증가분 합) × 역할별 가중 토큰 비중.
    사용자 본인의 대화형 사용도 섞여 있는 추정치입니다."""
    total = sum(run.role_wtok.values())
    if not total:
        return
    run.event("role_usage", total_util_pct=round(run.meter.spent, 2),
              per_role={r: {"wtok": round(w, 1), "share": round(w / total, 3), "util_pct": round(run.meter.spent * w / total, 2)}
                        for r, w in run.role_wtok.items()})


def _discard():
    git("checkout", "-q", "--", ".")
    git("clean", "-fdq")              # 추적되지 않는 새 파일 제거(빌드 산출물은 .git/info/exclude로 보호)
    git("checkout", "-q", BEST)


def _plan(run, it, roles, best, attempts_txt, used_titles, reason, plan_no):
    """가설 후보를 받아 분석 회차별로 고유 id를 붙이고, 이번 런에서 이미 배정한 제목과 같은 것은 중복으로 표시합니다."""
    checkpoint(run, it, "분석 전")
    text, _, _ = run_role(run, it, "planner", roles_mod.planner_prompt(SPEC, best, attempts_txt, used_titles),
                          roles, roles_mod.READ_ONLY_TOOLS)
    items = roles_mod.parse_hypotheses(text)
    seen = {t.strip().lower() for t in used_titles}
    for h in items or []:
        h["id"] = f"p{plan_no}-{h['id']}"
        if h["title"].strip().lower() in seen:
            h["duplicate"] = True
    run.event("hypotheses", iteration=it, reason=reason, items=items or [], ok=items is not None,
              new=sum(1 for h in items or [] if not h.get("duplicate")))
    return items or []


def _iterate(run, dry_run, iterations, best, noise, history, roles, allocation):
    end_extra = {}
    auto = alloc_mod.AutoAllocator(roles) if allocation.get("mode") == "auto" else None
    hypotheses, used_ids, used_titles = [], set(), []
    need_plan = "start"               # 다음에 planner를 돌릴 이유 (start | plateau | accept | exhausted | None)
    plateau_plans = 0
    last_plan_it = 0                  # 정체는 마지막 분석 이후의 결과로만 셉니다(정체 중 매 반복 재분석 방지)
    plan_no = 0
    stop_recommended = False
    budget_pct = allocation.get("run_budget_pct")

    for it in range(1, iterations + 1):
        ctl = run.control()
        if ctl.get("stop_after"):
            end_extra = {"reason": "사용자 요청: 반복 종료 후 정지", "command_id": ctl.get("stop_after_command_id")}
            break
        checkpoint(run, it, "반복 시작 전")
        apply_role_override(run, it, roles, auto)
        ok, why = usage_ok(run)
        if not ok and not wait_for_usage(run, why):
            end_extra = {"reason": "사용량 대기 중 정지 요청", "command_id": run.control().get("stop_after_command_id")}
            break
        left = run.meter.budget_left(budget_pct)
        if left is not None and not dry_run and left < run.meter.avg_iteration_cost():
            end_extra = {"reason": f"런 사용량 예산 소진 (남은 {left:.1f}%p < 반복 평균 {run.meter.avg_iteration_cost():.1f}%p)"}
            break
        if auto and not dry_run:
            changes, reason = auto.adjust(roles, it, (run.last_rate_limit or {}).get("five_hour"), left, budget_pct)
            if changes:
                run.event("roles_changed", iteration=it, by="auto", changes=changes, reason=reason)

        branch = f"rsi/iter-{run.id}-{it}"
        git("checkout", "-q", "-B", branch, BEST)
        run.event("iteration_start", iteration=it, branch=branch)
        rec = {"iteration": it}
        diff_lines = 0
        spent_before = run.meter.spent
        attempts_txt = roles_mod.attempts_text(load_attempts(allocation.get("attempts_in_prompt", 15)), metric())

        if not dry_run:
            # ---- planner: 시작·정체·채택 후·가설 소진 시 (when 설정이 허용할 때)
            p_when = roles.get("planner", {}).get("when", "off") if alloc_mod.role_active(roles, "planner") else "off"
            allowed = {"start": p_when != "off",
                       "plateau": p_when in ("start_plateau", "start_plateau_accept") or (auto is not None and p_when != "off"),
                       "accept": p_when == "start_plateau_accept",
                       "exhausted": p_when in ("start_plateau", "start_plateau_accept")}
            if need_plan and allowed.get(need_plan):
                plan_no += 1
                hypotheses = _plan(run, it, roles, best, attempts_txt, used_titles, need_plan, plan_no)
                last_plan_it = it
                if need_plan == "plateau":
                    plateau_plans += 1
            need_plan = None
            assigned = next((h for h in hypotheses if h["id"] not in used_ids and not h.get("stale") and not h.get("duplicate")), None)
            if assigned:
                used_ids.add(assigned["id"]); used_titles.append(assigned["title"])
                rec["assigned"] = assigned["title"]
                run.event("hypothesis_assigned", iteration=it, id=assigned["id"], title=assigned["title"])

            # ---- implementer
            injections = take_injections(run)
            if injections:
                run.event("instruction_applied", iteration=it, command_ids=[i["command_id"] for i in injections],
                          texts=[i["text"] for i in injections])
            text, result_msg, timed_out = run_role(run, it, "implementer",
                                                   build_prompt(it, best, attempts_txt, injections, assigned),
                                                   roles, SPEC["agent"].get("tools", "Read,Edit,Write,Glob,Grep"))
            summary = next((l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith("SUMMARY:")), text[:200])
            hypothesis = next((l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith("HYPOTHESIS:")), "")
            usage = (result_msg or {}).get("usage", {})
            run.event("agent_done", iteration=it, timed_out=timed_out, duration_ms=(result_msg or {}).get("duration_ms"),
                      num_turns=(result_msg or {}).get("num_turns"), output_tokens=usage.get("output_tokens"),
                      summary=summary, hypothesis=hypothesis, assigned=rec.get("assigned"))
            rec.update(summary=summary, hypothesis=hypothesis)
            checkpoint(run, it, "구현 완료 후")
            files = changed_files()
            if timed_out or not files:
                rec["decision"] = "agent_timeout" if timed_out else "no_change"
                run.event("judgment", iteration=it, decision=rec["decision"])
                _discard(); _finish_iteration(run, rec, best, None, history, spent_before)
                continue
            bad = out_of_scope(files)
            if bad:
                rec["decision"] = "rejected_scope"
                run.event("gate", iteration=it, gate="scope", passed=False, detail=", ".join(bad))
                run.event("judgment", iteration=it, decision="rejected_scope", files=bad)
                _discard(); _finish_iteration(run, rec, best, None, history, spent_before)
                continue
            git("add", "-A")
            git("commit", "-q", "-m", f"rsi iter {it}: {summary[:72]}")
            stat = git("diff", "--shortstat", f"{BEST}..HEAD")
            diff_lines = sum(int(n) for n in re.findall(r"(\d+) (?:insertion|deletion)", stat))
            run.event("candidate_commit", iteration=it, commit=git("rev-parse", "--short", "HEAD"), files=files, diff_stat=stat)

        # ---- 평가
        result = evaluate(run, f"iter{it}", best=None if dry_run else best, iteration=it)
        for g in result["gates"]:
            run.event("gate", iteration=it, gate=g["gate"], passed=g["passed"], detail=g.get("detail"))
        failed = [g for g in result["gates"] if not g["passed"]]
        obj = result.get("objective")
        if failed or obj is None:
            gate = failed[0]["gate"] if failed else "eval"
            base_gate = gate.split(":", 1)[1] if ":" in gate and not gate.startswith("promote:") else gate
            early = gate.startswith("promote:") or base_gate == "early_stop"
            rec["decision"] = "rejected_early" if early else {"build": "rejected_build", "snapshot": "rejected_snapshot",
                                                              "no_page_errors": "rejected_errors"}.get(base_gate, "rejected_gate")
            if result.get("stage_reached"):
                rec["stage_reached"] = result["stage_reached"]
            rec["failed_gate"] = gate
            run.event("judgment", iteration=it, decision=rec["decision"], failed_gate=gate)
            git("checkout", "-q", BEST); _finish_iteration(run, rec, best, result, history, spent_before)
            continue

        rec["objective"] = obj
        gain = improvement(best["objective"], obj)
        rec["gain"] = round(gain, 3)
        run.event("metric", iteration=it, objective=obj, breakdown=result.get("breakdown"), best_objective=best["objective"])
        best_before = best
        if dry_run:
            rec["decision"] = "measured"
        elif gain > noise:
            # ---- reviewer: 채택 후보에만. 거부·고위험·형식 오류면 판정 정책과 관계없이 사람 승인으로.
            reason = None
            if alloc_mod.role_active(roles, "reviewer"):
                checkpoint(run, it, "검토 전")
                diff_text = git("diff", f"{BEST}..HEAD")[:60000]
                rtext, _, _ = run_role(run, it, "reviewer",
                                       roles_mod.reviewer_prompt(SPEC, diff_text, rec.get("summary"), rec.get("hypothesis"),
                                                                 round(gain, 3), metric()),
                                       roles, roles_mod.READ_ONLY_TOOLS)
                review = roles_mod.parse_review(rtext)
                run.event("review", iteration=it, ok=review is not None,
                          **(review or {"verdict": "unknown", "risk": "unknown", "reasons": ["검토 출력 형식 오류"]}))
                rec["review"] = review["verdict"] if review else "unknown"
                if review is None:
                    reason = "검토 결과를 해석하지 못함 — 사람 확인 필요"
                elif review["verdict"] == "reject" or review["risk"] == "high":
                    reason = f"검토 {'거부' if review['verdict'] == 'reject' else '고위험'}: " + " / ".join(review["reasons"][:3])
            reason = reason or needs_human(gain, best["objective"], diff_lines)
            approved = True
            if reason:
                approved = wait_for_approval(run, it, {"objective": obj, "improvement": round(gain, 3), "reason": reason,
                                                       "commit": git("rev-parse", "--short", "HEAD"), "diff_lines": diff_lines})
            if approved:
                git("checkout", "-q", BEST)
                git("merge", "-q", "--ff-only", branch)
                best = result
                save_best(git("rev-parse", "--short", BEST), result)
                rec["decision"] = "accepted"
                # best가 바뀌면 남은 가설의 전제가 달라지므로 낡음 처리하고 재계획합니다.
                for h in hypotheses:
                    h["stale"] = True
                need_plan = "accept"
            else:
                rec["decision"] = "rejected_by_human"
        else:
            rec["decision"] = "rejected_no_gain"
        git("checkout", "-q", BEST)
        run.event("judgment", iteration=it, decision=rec["decision"], improvement=round(gain, 3))
        _finish_iteration(run, rec, best_before, result, history, spent_before)

        # ---- 다음 반복의 planner 필요 여부(정체·가설 소진)
        if not dry_run and need_plan is None:
            streak = 0
            for h in reversed([h for h in history if h["iteration"] >= max(last_plan_it, 1)]):
                if h["decision"] in ("rejected_no_gain", "no_change", "rejected_build", "rejected_snapshot", "rejected_gate", "rejected_early"):
                    streak += 1
                else:
                    break
            if streak >= allocation.get("plateau_after", 2):
                if plateau_plans >= 2:
                    # 재분석을 두 번 해도 정체면 더 분석하지 않고(사용량 절약) 종료를 권고합니다. 실제 종료는 사용자 판단.
                    if not stop_recommended:
                        run.event("stop_recommended", iteration=it,
                                  reason=f"정체 {streak}회 — 분석을 {plateau_plans}번 다시 했지만 개선이 없습니다. 런 종료를 권장합니다.")
                        stop_recommended = True
                else:
                    need_plan = "plateau"
            # 후보를 다 쓴 경우에만 재분석합니다. 마지막 분석이 새 가설을 하나도 못 냈다면 정체가 쌓일 때까지 기다립니다
            # (그렇지 않으면 매 반복 분석을 다시 돌려 사용량을 낭비함 — 검증에서 확인).
            elif any(not h.get("duplicate") for h in hypotheses) and \
                    all(h["id"] in used_ids or h.get("stale") or h.get("duplicate") for h in hypotheses):
                need_plan = "exhausted"

    run.event("run_end", best_ref=git("rev-parse", "--short", BEST), best_objective=best["objective"],
              decisions=[h["decision"] for h in history], **end_extra)
    emit_role_usage(run)
    (run.dir / "best.json").write_text(json.dumps(best, indent=2, ensure_ascii=False))
    cleanup_eval()


def _finish_iteration(run, rec, best_before, result, history, spent_before):
    run.meter.iteration_costs.append(run.meter.spent - spent_before)
    history.append(rec)
    if rec.get("decision") != "measured":
        record_attempt(run, rec, best_before, result)


if __name__ == "__main__":
    main()
