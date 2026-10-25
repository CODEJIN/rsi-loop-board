#!/usr/bin/env python3
"""RSI Loop 오케스트레이터 — 호스트에서 상시 실행(systemd 사용자 서비스 rsi-orchestrator).

대시보드 DB의 명령 큐(commands)를 1초마다 확인해서 실행하고, heartbeat와 상태를 orchestrator 테이블에 남깁니다.
루프 자체(loop.py)는 별도 프로세스로 띄우고, 실행 중 제어는 런 폴더의 control.json으로 전달합니다:
  start_run → loop.py 실행          stop       → SIGTERM (즉시 중단)
  pause/resume/inject/stop_after → control.json 갱신 (루프가 체크포인트에서 읽고 반영 이벤트를 남김)
명령이 실제로 반영되면 루프가 이벤트에 command_id를 남기고, 대시보드가 그걸 보고 '반영됨'으로 바꿉니다.
"""
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("RSI_DB_PATH", str(ROOT.parent / "dashboard" / "data" / "rsi.db")))
RUNS_DIR = ROOT / "runs"
import nodes as nodes_mod

NODE_CHECK_SEC = 60
DESIGN_PROMPT = ROOT / "design_prompt.md"
WORKSPACES = ROOT.parent / "workspaces"
EVALS = ROOT / "evals"
STATE_DIR = ROOT / "state"
GIT_STATE = {"none": "아님 (git init 필요)", "empty": "git이지만 커밋 없음", "ok": "있음"}
INIT_MAX_FILES, INIT_MAX_BYTES = 20000, 300 * 10**6    # 첫 커밋 한도 — 넘으면 데이터·빌드 산출물이 섞였을 가능성이 큼
DESIGN_TIMEOUT_SEC = 30 * 60
POLL_SEC = 1.0
HEARTBEAT_SEC = 5.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("orchestrator")


def heavy_paths(repo, min_files=300, min_bytes=10 * 10**6):
    """git에 넣기 전 원본의 최상위 큰 폴더·파일 — 데이터·미디어·산출물일 가능성이 커서 .gitignore 후보로 보여줍니다."""
    out = []
    for p in sorted(repo.iterdir()):
        if p.name == ".git" or p.is_symlink():
            continue
        if p.is_file():
            size, files = p.stat().st_size, 1
        else:
            size = files = 0
            for root, _, names in os.walk(p):
                for n in names:
                    try:
                        size += os.lstat(os.path.join(root, n)).st_size
                    except OSError:
                        pass
                files += len(names)
                if files > 200000:
                    break
        if files >= min_files or size >= min_bytes:
            out.append({"path": p.name + ("/" if p.is_dir() else ""), "files": files, "mb": round(size / 1e6, 1)})
    return out


def now():
    return datetime.now().isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def list_specs():
    """대시보드 DB의 Spec 목록(이름별 최신 버전) — heartbeat로 함께 보고합니다."""
    try:
        with connect() as conn:
            rows = conn.execute("SELECT name, MAX(version) AS version FROM spec_versions GROUP BY name").fetchall()
        return [{"name": r["name"], "version": r["version"]} for r in rows]
    except sqlite3.OperationalError:
        return []


def setting(key, default=None):
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def snapshot_spec(text, payload):
    """런 폴더에 넘길 Spec 스냅샷 — 대시보드 전역 설정(모델 계층·계층 가중치)과 런 시작 시 역할·할당 덮어쓰기를 합칩니다.
    루프는 DB를 읽지 않으므로, 이 스냅샷이 그 런의 설정 전부가 됩니다(재현성)."""
    import yaml
    data = yaml.safe_load(text)
    tiers = setting("model_tiers")
    if tiers:
        data["model_tiers"] = {**(data.get("model_tiers") or {}), **tiers}
    factors = setting("tier_factor")
    if factors:
        data["tier_factor"] = factors
    if payload.get("roles"):
        roles = data.setdefault("roles", {})
        for name, patch in payload["roles"].items():
            roles[name] = {**(roles.get(name) or {}), **patch}
    if payload.get("allocation"):
        data["allocation"] = {**(data.get("allocation") or {}), **payload["allocation"]}
    # 실행 노드 목록 — 루프가 evaluation.node / evaluation.matrix 를 해석할 때 씁니다(비밀값 없음, 키는 노드 전용 파일).
    with connect() as conn:
        data["nodes"] = {r["id"]: {k: r[k] for k in ("transport", "host", "port", "user", "shell", "os", "workdir")}
                         | {"tags": json.loads(r["tags"] or "[]")}
                         for r in conn.execute("SELECT * FROM nodes WHERE enabled=1")}
    header = f"# 런 스냅샷: {data.get('spec')} v{data.get('version')} + 대시보드 설정·런 덮어쓰기 ({now()})\n"
    return header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def spec_yaml(name, version):
    with connect() as conn:
        row = conn.execute("SELECT yaml FROM spec_versions WHERE name=? AND version=?", (name, version)).fetchone()
    return row["yaml"] if row else None


def schedule_window(sched, now):
    """일정의 시간 창 안이면 (창 시작, 창 끝). 끝이 시작보다 이르면 자정을 넘는 창 (대시보드 insights.window_for와 같은 규칙)."""
    from datetime import timedelta
    days = json.loads(sched["days"])
    sh, sm = map(int, sched["start_time"].split(":"))
    eh, em = map(int, sched["end_time"].split(":"))
    for back in (0, 1):
        start = (now - timedelta(days=back)).replace(hour=sh, minute=sm, second=0, microsecond=0)
        end = start.replace(hour=eh, minute=em)
        if end <= start:
            end += timedelta(days=1)
        if start.weekday() in days and start <= now < end:
            return start, end
    return None


class AdoptedProc:
    """오케스트레이터 재시작 전에 띄운 루프 — 종료 코드는 모르지만 살아 있는지와 중단은 다룰 수 있습니다."""
    def __init__(self, pid):
        self.pid = pid
        self.returncode = None

    def poll(self):
        try:
            os.kill(self.pid, 0)
            cmdline = Path(f"/proc/{self.pid}/cmdline").read_bytes()
            return None if b"loop.py" in cmdline else 0
        except (ProcessLookupError, FileNotFoundError):
            return 0


def find_live_loop():
    for pid_file in sorted(RUNS_DIR.glob("*/loop.pid"), reverse=True):
        events = pid_file.parent / "events.jsonl"
        tail = events.read_text().splitlines()[-1:] if events.exists() else []
        if tail and json.loads(tail[0])["type"] in ("run_end", "run_aborted"):
            continue
        proc = AdoptedProc(int(pid_file.read_text()))
        if proc.poll() is None:
            return pid_file.parent.name, proc
    return None, None


class Orchestrator:
    def __init__(self):
        self.proc = None          # 실행 중인 loop.py
        self.run_id = None
        self.started_cmd = None
        self.last_heartbeat = 0
        self.last_schedule_check = 0
        self.last_node_check = 0
        self.node_checking = set()
        self.nodes_ready = self.init_nodes()
        self.scheduled = None      # (schedule_id, run_id, 창 끝) — 창이 끝나면 '이번 반복 후 종료'
        self.stop_sent_for = None
        run_id, proc = find_live_loop()
        if proc:
            log.info("실행 중인 루프 %s(pid %s)를 이어서 관리합니다", run_id, proc.pid)
            self.run_id, self.proc = run_id, proc

    # ---- 상태 보고
    def heartbeat(self, force=False):
        if not force and time.time() - self.last_heartbeat < HEARTBEAT_SEC:
            return
        state = {"pid": os.getpid(), "active_run": self.run_id if self.running() else None,
                 "specs": list_specs(), "host": os.uname().nodename}
        with connect() as conn:
            conn.execute("INSERT INTO orchestrator(name, heartbeat_at, state) VALUES ('main', ?, ?) "
                         "ON CONFLICT(name) DO UPDATE SET heartbeat_at=excluded.heartbeat_at, state=excluded.state",
                         (now(), json.dumps(state, ensure_ascii=False)))
        self.last_heartbeat = time.time()

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    # ---- 명령 처리
    def set_status(self, cid, status, result=None, run_id=None):
        with connect() as conn:
            conn.execute("UPDATE commands SET status=?, result=COALESCE(?, result), run_id=COALESCE(run_id, ?), "
                         "delivered_at=CASE WHEN ?='delivered' THEN ? ELSE delivered_at END WHERE id=?",
                         (status, result, run_id, status, now(), cid))

    def control_path(self, run_id):
        return RUNS_DIR / run_id / "control.json"

    def update_control(self, run_id, fn):
        path = self.control_path(run_id)
        ctl = json.loads(path.read_text()) if path.exists() else {}
        fn(ctl)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(ctl, ensure_ascii=False, indent=1))
        tmp.replace(path)     # 루프가 반쯤 쓰인 파일을 읽지 않도록 원자적으로 교체

    # ---- 설계(Design Studio)
    def design_message(self, cid, payload):
        """설계 세션의 사용자 메시지 하나에 대해 Claude Code를 실행하고, 답변을 DB에 실시간으로 채웁니다(별도 스레드)."""
        sid, mid = payload["session_id"], payload["message_id"]
        with connect() as conn:
            sess = conn.execute("SELECT * FROM design_sessions WHERE id=?", (sid,)).fetchone()
            row = conn.execute("SELECT text, meta FROM design_messages WHERE id=?", (mid,)).fetchone()
            first = conn.execute("SELECT COUNT(*) FROM design_messages WHERE session_id=? AND role='assistant'", (sid,)).fetchone()[0] == 0
            aid = conn.execute("INSERT INTO design_messages(session_id, role, text, meta, ts) VALUES (?, 'assistant', '', ?, ?)",
                               (sid, json.dumps({"streaming": True}), now())).lastrowid

        def finish(status, error=None):
            with connect() as conn:
                conn.execute("UPDATE design_sessions SET status=?, last_error=?, updated_at=? WHERE id=?", (status, error, now(), sid))
            self.set_status(cid, "applied" if status == "idle" else "failed", error)

        repo = Path(sess["repo"])
        if not repo.is_dir():
            with connect() as conn:
                conn.execute("UPDATE design_messages SET text=?, meta=? WHERE id=?",
                             (f"⚠️ 저장소 경로를 찾을 수 없습니다: {repo}", json.dumps({"error": True}), aid))
            return finish("error", "저장소 경로 없음")
        # 도우미가 실행 결과·원본 상태를 알 수 있도록 대시보드 상태를 메시지 앞에 붙입니다(화면의 말풍선에는 보이지 않음).
        git = self.record_repo_git(sid, repo)
        context = (json.loads(row["meta"]) if row["meta"] else {}).get("context") or "[대시보드 상태 — 사용자가 아니라 시스템이 붙인 정보]"
        context += f"\n- 원본 저장소 git: {GIT_STATE[git]}"
        with connect() as conn:
            heavy = json.loads(conn.execute("SELECT repo_heavy FROM design_sessions WHERE id=?", (sid,)).fetchone()[0] or "[]")
        if heavy:
            context += "\n- 원본의 큰 폴더·파일(.gitignore 후보): " + ", ".join(f"{h['path']} {h['files']}개 {h['mb']}MB" for h in heavy)
        text = f"{context}\n\n{row['text']}"

        cmd = ["claude", "-p", text, "--output-format", "stream-json", "--verbose",
               "--tools", "Read,Glob,Grep", "--append-system-prompt", DESIGN_PROMPT.read_text(),
               "--strict-mcp-config", "--disable-slash-commands"]
        # 설계 대화 모델: 설정의 계층(기본 표준) → 모델 별칭. 긴 설계 답변은 출력 토큰이 많아 Pro에서는 표준 계층이 기본입니다.
        tier = setting("design_tier", "standard")
        model = (setting("model_tiers") or {}).get(tier) or {"high": "opus", "standard": "sonnet", "light": "haiku"}.get(tier)
        if model:
            cmd += ["--model", model]
        # 도우미 전용 도구(design_mcp.py): 준비 작업 다섯 가지만 직접 실행할 수 있는 좁은 권한
        mcp_cfg = STATE_DIR / f"design_mcp_{sid}.json"
        mcp_cfg.write_text(json.dumps({"mcpServers": {"rsi": {
            "command": sys.executable, "args": [str(ROOT / "design_mcp.py")],
            "env": {"RSI_DESIGN_SESSION": str(sid), "RSI_DB": str(DB_PATH)}}}}))
        cmd += ["--mcp-config", str(mcp_cfg), "--allowedTools", "mcp__rsi"]
        # 읽기 범위: 원본(링크·실제 경로 모두)과 루프 산출물. dashboard/.env 같은 비밀값이 있는 RSI_Loop 전체는 열지 않습니다.
        read_dirs = {str(repo), str(repo.resolve()), str(WORKSPACES), str(EVALS), str(RUNS_DIR)}
        cmd += ["--add-dir", *sorted(read_dirs)]
        cmd += ["--session-id", sess["claude_session_id"]] if first else ["--resume", sess["claude_session_id"]]
        proc = subprocess.Popen(cmd, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        timer = threading.Timer(DESIGN_TIMEOUT_SEC, proc.kill)
        timer.start()
        parts, meta, last_write, result = [], {"streaming": True, "tools": 0}, 0.0, None

        def flush(force=False):
            nonlocal last_write
            if force or time.time() - last_write > 0.7:
                with connect() as conn:
                    conn.execute("UPDATE design_messages SET text=?, meta=? WHERE id=?",
                                 ("\n\n".join(parts), json.dumps(meta, ensure_ascii=False), aid))
                    conn.execute("UPDATE design_sessions SET updated_at=? WHERE id=?", (now(), sid))
                last_write = time.time()

        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = msg.get("type")
            if t == "assistant":
                for block in msg.get("message", {}).get("content", []):
                    if block.get("type") == "text" and block.get("text", "").strip():
                        parts.append(block["text"])
                    elif block.get("type") == "tool_use":
                        meta["tools"] += 1
                        inp = block.get("input", {})
                        name = block.get("name", "")
                        if name.startswith("mcp__rsi__"):
                            # 도우미가 직접 실행한 준비 작업 — 말풍선에 실행 기록으로 보여줍니다(스크립트 내용은 빼고).
                            meta.setdefault("actions", []).append({"id": block.get("id"), "tool": name[len("mcp__rsi__"):],
                                                                   "input": {k: v for k, v in inp.items() if k not in ("content", "gitignore")},
                                                                   "lines": len(str(inp.get("content") or inp.get("gitignore") or "").splitlines())})
                            meta["last_tool"] = f"실행: {name[len('mcp__rsi__'):]}"
                        else:
                            meta["last_tool"] = f"{name} {inp.get('file_path') or inp.get('pattern') or ''}".strip()
                flush()
            elif t == "user":
                for block in msg.get("message", {}).get("content", []) if isinstance(msg.get("message", {}).get("content"), list) else []:
                    if block.get("type") != "tool_result":
                        continue
                    act = next((a for a in meta.get("actions", []) if a["id"] == block.get("tool_use_id")), None)
                    if act:
                        c = block.get("content")
                        act["result"] = (c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict)))[:400]
                        act["ok"] = not block.get("is_error")
                        flush(force=True)
            elif t == "rate_limit_event":
                windows = (msg.get("rate_limit_info") or {}).get("unifiedWindows") or {}
                with connect() as conn:
                    conn.execute("INSERT INTO settings(key, value) VALUES ('usage_latest', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                 (json.dumps({"ts": now(), "windows": {k: v.get("utilization") for k, v in windows.items()}, "source": "design"}),))
                    conn.execute("INSERT INTO usage_samples(ts, five_hour, seven_day, five_reset, seven_reset, source) VALUES (?, ?, ?, ?, ?, 'design')",
                                 (now(), (windows.get("five_hour") or {}).get("utilization"), (windows.get("seven_day") or {}).get("utilization"),
                                  (windows.get("five_hour") or {}).get("resetsAt"), (windows.get("seven_day") or {}).get("resetsAt")))
            elif t == "result":
                result = msg
        proc.wait()
        timer.cancel()
        meta.update(streaming=False, duration_ms=(result or {}).get("duration_ms"),
                    output_tokens=((result or {}).get("usage") or {}).get("output_tokens"))
        if not parts and result and result.get("result"):
            parts.append(result["result"])
        if result is None or result.get("is_error"):
            meta["error"] = True
            parts.append("⚠️ 답변 생성이 중단됐습니다" + (" (시간 초과)" if proc.returncode in (-9, 137) else "") + ". 다시 보내주세요.")
            flush(force=True)
            return finish("error", "답변 실패")
        flush(force=True)
        finish("idle")

    def save_eval_script(self, cid, payload):
        folder = EVALS / payload["folder"]
        path = folder / payload["filename"]
        # 저장 위치는 evals/<폴더>/ 안으로만 (하위 폴더 허용, 바깥으로 나가는 경로는 거부)
        if folder.resolve().parent != EVALS.resolve() or not path.resolve().is_relative_to(folder.resolve()):
            return self.set_status(cid, "rejected", "허용되지 않는 경로")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.with_suffix(path.suffix + ".bak").write_text(path.read_text())
        path.write_text(payload["content"])
        if path.suffix in (".py", ".sh"):
            path.chmod(0o755)
        self.set_status(cid, "applied", f"evals/{payload['folder']}/{payload['filename']} 저장")

    def delete_run(self, cid, payload):
        """런 폴더를 runs/_trash/ 로 옮깁니다(되돌릴 수 있게 지우지 않음). 대시보드 수집은 runs/*/events.jsonl만 보므로 더 이상 잡히지 않습니다."""
        rid = payload["run_id"]
        src = RUNS_DIR / rid
        if self.running() and self.run_id == rid:
            return self.set_status(cid, "rejected", "실행 중인 런은 삭제할 수 없습니다")
        if not src.is_dir() or "/" in rid or rid.startswith("."):
            return self.set_status(cid, "rejected", "런 폴더가 없습니다")
        trash = RUNS_DIR / "_trash"
        trash.mkdir(exist_ok=True)
        src.rename(trash / f"{rid}")
        self.set_status(cid, "applied", f"runs/_trash/{rid} 로 이동")

    def record_repo_git(self, sid, repo):
        """원본 저장소의 git 상태(none: git 아님, empty: 커밋 없음, ok)를 세션에 기록합니다 — 화면의 'git 초기화' 카드 완료 표시용."""
        state = "none" if not (repo / ".git").exists() else (
            "ok" if subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], cwd=repo, capture_output=True).returncode == 0 else "empty")
        heavy = heavy_paths(repo) if state != "ok" else []
        with connect() as conn:
            conn.execute("UPDATE design_sessions SET repo_git=?, repo_heavy=? WHERE id=?", (state, json.dumps(heavy, ensure_ascii=False), sid))
        return state

    def init_repo(self, cid, payload):
        """원본을 git 저장소로 만들고 첫 커밋 — 사용자가 설계 화면의 실행 카드에서 승인한 경우에만 옵니다.
        이미 커밋이 있으면 손대지 않고, 올릴 파일이 너무 많거나 크면 커밋하지 않고 멈춥니다(.gitignore 조정 필요)."""
        repo = Path(payload["repo"])
        if not repo.is_dir():
            return self.set_status(cid, "rejected", f"경로가 없습니다: {repo}")
        if self.record_repo_git(payload["session_id"], repo) == "ok":
            return self.set_status(cid, "applied", "이미 커밋이 있는 git 저장소입니다 (변경 없음)")
        g = lambda *a: subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True)
        gi, wanted = repo / ".gitignore", [l for l in payload.get("gitignore", "").splitlines() if l.strip()]
        if wanted:
            have = set(gi.read_text().splitlines()) if gi.exists() else set()
            add = [l for l in wanted if l not in have]
            if add:
                with open(gi, "a") as f:
                    f.write(("\n" if have else "") + "\n".join(add) + "\n")
        if not (repo / ".git").exists() and (r := g("init", "-q", "-b", "main")).returncode != 0:
            return self.set_status(cid, "failed", r.stderr[-300:])
        g("add", "-A")
        files = [f for f in g("diff", "--cached", "--name-only", "-z").stdout.split("\0") if f]
        size = sum((repo / f).stat().st_size for f in files if (repo / f).is_file())
        if len(files) > INIT_MAX_FILES or size > INIT_MAX_BYTES:
            g("reset", "-q")
            return self.set_status(cid, "rejected", f"올릴 파일이 너무 많거나 큽니다: {len(files)}개, {size / 1e6:.0f}MB "
                                   f"(한도 {INIT_MAX_FILES}개·{INIT_MAX_BYTES // 10**6}MB). .gitignore를 조정하세요.")
        ident = [] if g("config", "user.email").stdout.strip() else ["-c", "user.name=RSI Loop", "-c", "user.email=rsi-loop@localhost"]
        r = g(*ident, "commit", "-q", "-m", "RSI Loop: 초기 커밋")
        if r.returncode != 0:
            return self.set_status(cid, "failed", (r.stderr or r.stdout)[-300:])
        self.record_repo_git(payload["session_id"], repo)
        self.set_status(cid, "applied", f"git 초기화 + 첫 커밋 ({len(files)}개 파일, {size / 1e6:.1f}MB)")

    def create_workspace(self, cid, payload):
        src, dest = Path(payload["source"]), WORKSPACES / payload["name"]
        if not (src / ".git").exists():
            return self.set_status(cid, "rejected", f"git 저장소가 아닙니다: {src}")
        if dest.exists():
            return self.set_status(cid, "rejected", f"이미 있습니다: {dest}")
        sh = subprocess.run(["git", "clone", "-q", str(src), str(dest)], capture_output=True, text=True)
        if sh.returncode != 0:
            return self.set_status(cid, "failed", sh.stderr[-300:])
        # 평가 과정의 빌드 산출물이 '에이전트가 바꾼 파일'로 잡히지 않도록 제외 목록에 넣습니다.
        with open(dest / ".git" / "info" / "exclude", "a") as f:
            f.write("\n# RSI Loop 평가 산출물\nnode_modules/\ndist-cand/\n__pycache__/\n*.pyc\n")
        self.set_status(cid, "applied", f"{dest} 생성 (원본 {src})")

    def handle(self, cmd):
        cid, ctype, run_id = cmd["id"], cmd["type"], cmd["run_id"]
        payload = json.loads(cmd["payload"] or "{}")
        log.info("command #%s %s run=%s %s", cid, ctype, run_id, {k: v for k, v in payload.items() if k != "content"})

        if ctype == "design_message":
            self.set_status(cid, "delivered")
            threading.Thread(target=self._safe, args=(self.design_message, cid, payload), daemon=True).start()
            return
        if ctype == "node_check":
            self.set_status(cid, "delivered")
            return self.check_nodes(force_id=payload["node_id"], cid=cid)
        if ctype == "delete_run":
            return self.delete_run(cid, payload)
        if ctype == "save_eval_script":
            return self.save_eval_script(cid, payload)
        if ctype == "create_workspace":
            return self.create_workspace(cid, payload)
        if ctype == "init_repo":
            return self.init_repo(cid, payload)
        if ctype == "repo_check":
            with connect() as conn:
                sess = conn.execute("SELECT repo FROM design_sessions WHERE id=?", (payload["session_id"],)).fetchone()
            if not sess or not Path(sess["repo"]).is_dir():
                return self.set_status(cid, "rejected", "저장소 경로 없음")
            return self.set_status(cid, "applied", self.record_repo_git(payload["session_id"], Path(sess["repo"])))

        if ctype == "start_run":
            if self.running():
                return self.set_status(cid, "rejected", f"이미 실행 중인 런이 있습니다 ({self.run_id})")
            text = spec_yaml(payload.get("spec"), payload.get("version"))
            if not text:
                return self.set_status(cid, "rejected", "Spec 버전을 찾을 수 없습니다")
            new_id = datetime.now().strftime("%Y%m%d-%H%M%S")
            (RUNS_DIR / new_id).mkdir(parents=True, exist_ok=True)
            # 런에 쓰인 Spec을 런 폴더에 그대로 남깁니다(나중에 Spec이 바뀌어도 이 런이 무엇으로 돌았는지 재현 가능).
            (RUNS_DIR / new_id / "spec.yaml").write_text(snapshot_spec(text, payload))
            out = open(RUNS_DIR / new_id / "loop.log", "w")
            self.proc = subprocess.Popen(
                [sys.executable, str(ROOT / "loop.py"), "--spec", str(RUNS_DIR / new_id / "spec.yaml"), "--iterations", str(payload["iterations"]),
                 "--run-id", new_id, "--command-id", str(cid)] + (["--dry-run"] if payload.get("dry_run") else []),
                cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
            self.run_id = new_id
            ps = getattr(self, "pending_schedule", None)
            if ps and ps[1] == cid:
                self.scheduled = (ps[0], new_id, ps[2])
                with connect() as conn:
                    conn.execute("UPDATE schedules SET last_run_id=? WHERE id=?", (new_id, ps[0]))
            return self.set_status(cid, "delivered", f"pid {self.proc.pid}", run_id=new_id)

        if run_id != self.run_id or not self.running():
            return self.set_status(cid, "rejected", "이 오케스트레이터가 실행 중인 런이 아닙니다")

        if ctype == "stop":
            # 루프가 run_aborted 이벤트에 이 명령 번호를 남기도록 먼저 기록해 둡니다.
            self.update_control(run_id, lambda c: c.update(stop_command_id=cid))
            os.killpg(self.proc.pid, signal.SIGTERM)
            return self.set_status(cid, "delivered", "SIGTERM 전송")
        if ctype == "pause":
            self.update_control(run_id, lambda c: c.update(pause=True, pause_command_id=cid))
        elif ctype == "resume":
            self.update_control(run_id, lambda c: c.update(pause=False, resume_command_id=cid))
        elif ctype == "stop_after":
            self.update_control(run_id, lambda c: c.update(stop_after=True, stop_after_command_id=cid))
        elif ctype == "set_roles":
            self.update_control(run_id, lambda c: c.update(roles_override={"roles": payload["roles"], "command_id": cid}))
        elif ctype in ("approve", "reject"):
            it = str(payload["iteration"])
            self.update_control(run_id, lambda c: c.setdefault("approvals", {}).update(
                {it: {"approved": ctype == "approve", "command_id": cid}}))
        elif ctype == "inject":
            self.update_control(run_id, lambda c: c.setdefault("injections", []).append(
                {"command_id": cid, "text": payload["text"], "at": now()}))
        else:
            return self.set_status(cid, "rejected", "지원하지 않는 명령")
        self.set_status(cid, "delivered")

    def _safe(self, fn, cid, payload):
        try:
            fn(cid, payload)
        except Exception as e:
            log.exception("command #%s 실패", cid)
            self.set_status(cid, "failed", str(e)[:300])
            if "session_id" in payload:
                with connect() as conn:
                    conn.execute("UPDATE design_sessions SET status='error', last_error=? WHERE id=?", (str(e)[:300], payload["session_id"]))

    # ---- 실행 노드
    def init_nodes(self):
        """노드 전용 SSH 키를 준비해 공개키를 대시보드에 알리고, 홈서버(local) 노드를 기본으로 등록합니다."""
        try:
            pub = nodes_mod.ensure_key()
            with connect() as conn:
                conn.execute("INSERT INTO settings(key, value) VALUES ('node_ssh_pubkey', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (json.dumps(pub),))
                conn.execute("INSERT OR IGNORE INTO nodes(id, name, transport, shell, os, tags, workdir, enabled, created_at) "
                             "VALUES ('server', '홈서버', 'local', 'bash', 'linux', '[\"default\"]', NULL, 1, ?)", (now(),))
            return True
        except Exception as e:
            log.warning("노드 초기화 실패(다음 확인 때 재시도): %s", e)
            return False

    def _check_node(self, node_id, cid=None):
        try:
            with connect() as conn:
                row = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
            if not row:
                if cid:
                    self.set_status(cid, "rejected", "노드 없음")
                return
            online, info, err = nodes_mod.probe(dict(row))
            with connect() as conn:
                conn.execute("UPDATE nodes SET online=?, last_check=?, info=CASE WHEN ? THEN ? ELSE info END, error=? WHERE id=?",
                             (int(online), now(), int(online), json.dumps(info, ensure_ascii=False), err, node_id))
            if cid:
                self.set_status(cid, "applied" if online else "failed", "연결됨" if online else err)
        finally:
            self.node_checking.discard(node_id)

    def check_nodes(self, force_id=None, cid=None):
        """1분마다 켜진 노드의 연결·사양을 확인합니다. SSH 시간 초과가 명령 처리를 막지 않도록 스레드에서 실행합니다."""
        if not self.nodes_ready:
            self.nodes_ready = self.init_nodes()   # 대시보드가 테이블을 만들기 전에 시작된 경우
            if not self.nodes_ready:
                return
        if force_id is None and time.time() - self.last_node_check < NODE_CHECK_SEC:
            return
        if force_id is None:
            self.last_node_check = time.time()
        with connect() as conn:
            ids = [force_id] if force_id else [r["id"] for r in conn.execute("SELECT id FROM nodes WHERE enabled=1")]
        for nid in ids:
            if nid in self.node_checking:
                continue
            self.node_checking.add(nid)
            threading.Thread(target=self._check_node, args=(nid, cid if nid == force_id else None), daemon=True).start()

    def check_schedules(self):
        """30초마다: 시간 창이 열린 일정이 있고 실행 중인 런이 없으면 시작 명령을 넣고, 일정으로 시작한 런은 창이 끝나면 정리합니다.
        명령은 대시보드와 같은 큐(commands)를 거치므로 화면의 명령 이력에도 그대로 남습니다."""
        if time.time() - self.last_schedule_check < 30:
            return
        self.last_schedule_check = time.time()
        now_dt = datetime.now()
        with connect() as conn:
            if self.scheduled and self.running() and self.run_id == self.scheduled[1] and now_dt >= self.scheduled[2] \
                    and self.stop_sent_for != self.run_id:
                conn.execute("INSERT INTO commands(run_id, type, payload, status, created_at) VALUES (?, 'stop_after', ?, 'queued', ?)",
                             (self.run_id, json.dumps({"reason": "일정 시간 창 종료"}), now()))
                self.stop_sent_for = self.run_id
                log.info("일정 %s 창 종료 → 런 %s 이번 반복 후 종료", self.scheduled[0], self.run_id)
            if self.running():
                return
            for s in conn.execute("SELECT * FROM schedules WHERE enabled=1").fetchall():
                w = schedule_window(s, now_dt)
                if not w or (s["last_started_at"] and datetime.fromisoformat(s["last_started_at"]) >= w[0]):
                    continue
                version = s["version"] or conn.execute("SELECT MAX(version) FROM spec_versions WHERE name=?", (s["spec"],)).fetchone()[0]
                if not version:
                    continue
                payload = {"spec": s["spec"], "version": version, "iterations": s["iterations"], "dry_run": bool(s["dry_run"]),
                           "schedule_id": s["id"]}
                cid = conn.execute("INSERT INTO commands(run_id, type, payload, status, created_at) VALUES (NULL, 'start_run', ?, 'queued', ?)",
                                   (json.dumps(payload), now())).lastrowid
                conn.execute("UPDATE schedules SET last_started_at=? WHERE id=?", (now(), s["id"]))
                self.pending_schedule = (s["id"], cid, w[1])
                log.info("일정 %s 시작 → 명령 #%s (창 끝 %s)", s["id"], cid, w[1])
                break

    def reap(self):
        if self.proc is not None and self.proc.poll() is not None:
            log.info("loop %s 종료 (code %s)", self.run_id, self.proc.returncode)
            self.proc = None
            self.heartbeat(force=True)

    def serve(self):
        log.info("orchestrator 시작 — DB %s", DB_PATH)
        stop = {"flag": False}
        signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
        while not stop["flag"]:
            try:
                self.reap()
                self.check_schedules()
                self.check_nodes()
                with connect() as conn:
                    queued = conn.execute("SELECT * FROM commands WHERE status='queued' AND type NOT IN ('save_spec') ORDER BY id").fetchall()   # save_spec은 대시보드(보드 상태 계산)가 처리
                for cmd in queued:
                    try:
                        self.handle(cmd)
                    except Exception as e:
                        log.exception("command #%s 실패", cmd["id"])
                        self.set_status(cmd["id"], "failed", str(e)[:300])
                self.heartbeat(force=bool(queued))
            except sqlite3.OperationalError as e:
                log.warning("DB 접근 실패 (대시보드가 아직 안 떴을 수 있음): %s", e)
            time.sleep(POLL_SEC)
        # 오케스트레이터가 내려가도 실행 중인 루프는 그대로 둡니다(별도 세션). 재시작 후에는 제어할 수 없으므로 로그로 남김.
        if self.running():
            log.warning("오케스트레이터 종료 — 루프 %s(pid %s)는 계속 실행됩니다", self.run_id, self.proc.pid)


if __name__ == "__main__":
    Orchestrator().serve()
