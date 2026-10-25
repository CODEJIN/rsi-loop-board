#!/usr/bin/env python3
"""설계 도우미 전용 MCP 서버 — 도우미가 RSI 준비 작업을 직접 실행하고 런 결과를 확인하게 하는 좁은 권한.

Bash·Write 같은 범용 도구 대신, 정해진 작업 다섯 가지만 도구로 줍니다. 각 도구는 명령 큐(commands)에
요청을 넣고 처리될 때까지 기다렸다가 결과를 돌려줍니다. 실제 처리는 기존과 같이 오케스트레이터
(git·clone·스크립트 저장·런 시작)와 대시보드(Spec 저장)가 하므로, 감사 기록과 '요청 결과' 목록에 그대로 남습니다.

오케스트레이터가 설계 대화마다 `--mcp-config`로 띄우며, 세션 ID는 환경 변수 RSI_DESIGN_SESSION으로 받습니다.
프로토콜: stdio 위의 줄 단위 JSON-RPC 2.0 (initialize / tools/list / tools/call).
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

RUNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")
DB_PATH = os.environ.get("RSI_DB", os.path.join(os.path.dirname(__file__), "..", "dashboard", "data", "rsi.db"))
SID = int(os.environ.get("RSI_DESIGN_SESSION", "0"))
WAIT_SEC = 180
FINAL = {"applied", "failed", "rejected", "expired"}

TOOLS = [
    {"name": "init_repo",
     "description": "원본 저장소(설계 세션의 대상 저장소)를 git 저장소로 만들고 첫 커밋을 만든다. 이미 커밋이 있으면 아무것도 하지 않는다. "
                    "gitignore의 패턴 중 .gitignore에 없는 것만 추가한다. 데이터·미디어·빌드 산출물·비밀값(.env)은 반드시 넣어라. "
                    "올릴 파일이 2만 개 또는 300MB를 넘으면 커밋하지 않고 거부한다.",
     "inputSchema": {"type": "object", "properties": {"gitignore": {"type": "string", "description": "줄바꿈으로 구분한 .gitignore 패턴"}},
                     "required": ["gitignore"]}},
    {"name": "create_workspace",
     "description": "원본 저장소를 /home/heejo/RSI_Loop/workspaces/<name> 으로 git clone한다. 원본에 커밋이 있어야 한다. "
                    "Spec의 workspace.repo와 이름을 맞춰라.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "소문자·숫자·_- 이름"}}, "required": ["name"]}},
    {"name": "save_script",
     "description": "평가 스크립트(또는 보조 파일)를 phase0/evals/<folder>/<file> 에 저장한다. 이미 있으면 .bak으로 백업하고 덮어쓴다. "
                    "evaluation.command의 경로와 맞춰라.",
     "inputSchema": {"type": "object", "properties": {
         "folder": {"type": "string"}, "file": {"type": "string", "description": "예: eval.py, harness/run.mjs"},
         "content": {"type": "string", "description": "파일 전체 내용"}}, "required": ["folder", "file", "content"]}},
    {"name": "save_spec",
     "description": "Spec 보드에 누적된 현재 Spec(이전 답변들의 rsi-board 블록)을 새 버전으로 저장한다. 이번 답변에서 바꾼 값은 spec에 "
                    "바뀐 값만 중첩 형식으로 넣으면 합쳐서 저장한다. 필수 항목이 빠지면 거부된다.",
     "inputSchema": {"type": "object", "properties": {"spec": {"type": "object", "description": "바뀐 값만 (예: {metric: {noise_threshold: 30}})"}}}},
    {"name": "dry_run",
     "description": "저장된 최신 Spec 버전으로 '측정만' 런을 시작한다(에이전트 없이 현재 최고 버전을 반복 측정, 구독 사용량 없음). "
                    "작업 공간·평가 스크립트·Spec 저장이 끝난 뒤에만 써라. 런은 백그라운드에서 돌고, 결과는 이후 [대시보드 상태]로 온다.",
     "inputSchema": {"type": "object", "properties": {"spec": {"type": "string", "description": "Spec 이름"},
                                                      "iterations": {"type": "integer", "minimum": 1, "maximum": 10}},
                     "required": ["spec"]}},
    {"name": "run_status",
     "description": "런 상태를 본다. run_id를 주면 그 런의 상태·중단 사유·반복별 결과·최근 평가 결과(eval-*.json 요약)를, "
                    "spec만 주면 그 Spec의 최근 런 목록을 돌려준다.",
     "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "spec": {"type": "string"}}}},
    {"name": "run_log",
     "description": "런의 loop.log 끝부분(오류 추적 포함)을 본다.",
     "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, "lines": {"type": "integer", "minimum": 10, "maximum": 300}},
                     "required": ["run_id"]}},
    {"name": "stop_run",
     "description": "실행 중인 런을 멈춘다(현재 반복을 버리고 정리). 멈춘 것처럼 보이는 런을 다시 시작하기 전에 쓴다.",
     "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]}},
]
RUN_ID = __import__("re").compile(r"^[\w.-]{1,64}$")


def _short(v, n=1500):
    t = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return t if len(t) <= n else t[:n] + f" …(+{len(t) - n}자)"


def run_status(args):
    rid, spec = str(args.get("run_id") or ""), str(args.get("spec") or "")
    with connect() as conn:
        if not rid:
            rows = conn.execute("SELECT id, spec, spec_version, status, dry_run, planned_iterations, baseline_objective, best_objective, started_at, ended_at "
                                "FROM runs WHERE (?='' OR spec=?) ORDER BY started_at DESC LIMIT 8", (spec, spec)).fetchall()
            return True, json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=1) or "런이 없습니다"
        if not RUN_ID.match(rid):
            return False, "run_id 형식이 올바르지 않습니다"
        run = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    if not run:
        return False, f"런이 없습니다: {rid}"
    out = {"run": {k: run[k] for k in run.keys() if k not in ("source_path",)}}
    d = os.path.join(RUNS_DIR, rid)
    events = []
    try:
        with open(os.path.join(d, "events.jsonl")) as f:
            for line in f:
                e = json.loads(line)
                if e.get("type") in ("run_start", "run_end", "run_aborted", "baseline_measured", "iteration_start", "gate", "metric", "judgment", "agent_done", "review"):
                    events.append({k: v for k, v in e.items() if k not in ("run", "metric", "spec", "title", "repo")})
    except (OSError, json.JSONDecodeError):
        pass
    out["events"] = [_short(e, 600) for e in events[-12:]]
    evals = sorted((n for n in os.listdir(d) if n.startswith("eval-") and n.endswith(".json")), key=lambda n: os.path.getmtime(os.path.join(d, n))) if os.path.isdir(d) else []
    for n in evals[-2:]:
        try:
            with open(os.path.join(d, n)) as f:
                out[n] = _short(json.load(f), 2500)
        except (OSError, json.JSONDecodeError):
            pass
    return True, json.dumps(out, ensure_ascii=False, indent=1)


def run_log(args):
    rid = str(args.get("run_id") or "")
    if not RUN_ID.match(rid):
        return False, "run_id 형식이 올바르지 않습니다"
    n = args.get("lines") if isinstance(args.get("lines"), int) else 80
    try:
        with open(os.path.join(RUNS_DIR, rid, "loop.log"), errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return False, f"로그가 없습니다: {rid}"
    return True, _short("".join(lines[-max(10, min(n, 300)):]), 12000)


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def now():
    return datetime.now().isoformat(timespec="seconds")


def queue_and_wait(ctype, payload):
    payload = {**payload, "session_id": SID, "by": "assistant"}
    with connect() as conn:
        cid = conn.execute("INSERT INTO commands(run_id, type, payload, status, created_at) VALUES (NULL, ?, ?, 'queued', ?)",
                           (ctype, json.dumps(payload, ensure_ascii=False), now())).lastrowid
    end = time.time() + WAIT_SEC
    while time.time() < end:
        with connect() as conn:
            row = conn.execute("SELECT status, result FROM commands WHERE id=?", (cid,)).fetchone()
        if row["status"] in FINAL:
            return row["status"] == "applied", f"{row['status']}: {row['result'] or ''}"
        time.sleep(1)
    return False, "시간 초과: 오케스트레이터가 처리하지 않았습니다 (꺼져 있을 수 있음)"


def call(name, args):
    if not SID:
        return False, "설계 세션 정보가 없습니다"
    with connect() as conn:
        sess = conn.execute("SELECT repo FROM design_sessions WHERE id=?", (SID,)).fetchone()
    if not sess:
        return False, "설계 세션을 찾을 수 없습니다"
    if name == "init_repo":
        return queue_and_wait("init_repo", {"repo": sess["repo"], "gitignore": str(args.get("gitignore", ""))[:5000]})
    if name == "create_workspace":
        return queue_and_wait("create_workspace", {"source": sess["repo"], "name": str(args.get("name", ""))})
    if name == "save_script":
        return queue_and_wait("save_eval_script", {"folder": str(args.get("folder", "")), "filename": str(args.get("file", "")),
                                                    "content": str(args.get("content", ""))})
    if name == "save_spec":
        return queue_and_wait("save_spec", {"spec": args.get("spec") if isinstance(args.get("spec"), dict) else {}})
    if name == "dry_run":
        spec = str(args.get("spec", ""))
        with connect() as conn:
            v = conn.execute("SELECT MAX(version) FROM spec_versions WHERE name=?", (spec,)).fetchone()[0]
        if not v:
            return False, f"저장된 Spec이 없습니다: {spec} (먼저 save_spec)"
        n = args.get("iterations") if isinstance(args.get("iterations"), int) and 1 <= args["iterations"] <= 10 else 5
        ok, msg = queue_and_wait("start_run", {"spec": spec, "version": v, "iterations": n, "dry_run": True})
        return ok, f"{spec} v{v} 측정만 런 {n}회 — {msg}"
    if name == "run_status":
        return run_status(args)
    if name == "run_log":
        return run_log(args)
    if name == "stop_run":
        rid = str(args.get("run_id") or "")
        with connect() as conn:
            cid = conn.execute("INSERT INTO commands(run_id, type, payload, status, created_at) VALUES (?, 'stop', ?, 'queued', ?)",
                               (rid, json.dumps({"session_id": SID, "by": "assistant"}), now())).lastrowid
        time.sleep(3)
        with connect() as conn:
            row = conn.execute("SELECT status, result FROM commands WHERE id=?", (cid,)).fetchone()
        return row["status"] not in ("rejected", "failed"), f"정지 요청 {row['status']}: {row['result'] or ''}"
    return False, f"알 수 없는 도구: {name}"


def main():
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:          # 알림(notifications/*)은 응답하지 않음
            continue
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                      "serverInfo": {"name": "rsi", "version": "1.0"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            try:
                ok, text = call(params.get("name"), params.get("arguments") or {})
            except Exception as e:   # 도구 오류는 대화가 끊기지 않게 결과로 돌려줌
                ok, text = False, f"오류: {e}"
            result = {"content": [{"type": "text", "text": text}], "isError": not ok}
        elif method == "ping":
            result = {}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
