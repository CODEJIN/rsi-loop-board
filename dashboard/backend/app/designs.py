"""대화형 설계(Design Studio) API.

대시보드는 메시지를 저장하고 명령 큐에 design_message를 넣기만 합니다. 오케스트레이터가 대상 저장소에서
Claude Code(읽기 전용 도구)를 실행하고, 답변을 design_messages에 실시간으로 채웁니다.
답변 안의 ```yaml``` (spec: 포함) 블록은 Spec 초안으로, ```python``` 등 첫 줄이 "# file: 이름" 인 블록은
평가 스크립트 초안으로 뽑아서 화면 오른쪽 패널에 보여줍니다.
"""
import json
import os
import re
import uuid
from datetime import datetime

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import auth, db, specs

router = APIRouter(prefix="/api/design", dependencies=[Depends(auth.require_admin)])
CODE_BLOCK = re.compile(r"```([\w-]+)?\n(.*?)```", re.S)
FILE_HEADER = re.compile(r"^\s*(?:#|//)\s*file:\s*([\w.\-]+(?:/[\w.\-]+)*)\s*$", re.M)   # 하위 폴더 허용(harness/x.py)
SCRIPT_NAME = re.compile(r"^[\w\-]+(?:/[\w\-]+){0,3}\.(py|sh|mjs|js|json|txt|yaml|yml)$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")


def now():
    return datetime.now().isoformat(timespec="seconds")


def queue(conn, ctype, payload):
    conn.execute("INSERT INTO commands(run_id, type, payload, status, created_at) VALUES (NULL, ?, ?, 'queued', ?)",
                 (ctype, json.dumps(payload, ensure_ascii=False), now()))


def extract_drafts(messages):
    """assistant 메시지에서 가장 최근 Spec 초안과, 파일 이름별 최신 스크립트 초안을 뽑습니다."""
    spec_draft, scripts = None, {}
    for m in messages:
        if m["role"] != "assistant" or not m["text"]:
            continue
        for lang, body in CODE_BLOCK.findall(m["text"]):
            if (lang or "").lower() in ("rsi-board", "rsi-actions"):
                continue
            if (lang or "").lower() in ("yaml", "yml") and re.search(r"(?m)^spec:\s*\S", body):
                try:
                    data = yaml.safe_load(body)
                    errs = specs.validate(data)
                except yaml.YAMLError as e:
                    errs = [f"YAML 문법 오류: {e}"]
                spec_draft = {"yaml": body, "message_id": m["id"], "errors": errs}
            else:
                header = FILE_HEADER.search(body.split("\n", 1)[0] + "\n")
                if header:
                    scripts[header.group(1)] = {"filename": header.group(1), "lang": lang, "content": body, "message_id": m["id"]}
    return spec_draft, list(scripts.values())


# ---- Spec 보드: 답변마다 붙는 ```rsi-board``` 블록(바뀐 값만)을 차례로 누적해 현재 설계 상태를 만듭니다.
SECTIONS = ("target", "metric", "gates", "judge", "budget", "evaluation", "workspace")
SPEC_ORDER = ("spec", "version", "title", "project", "workspace", "target", "agent", "evaluation", "metric",
              "judge", "budget", "roles", "allocation")
SPEC_DEFAULTS = {"version": 1, "workspace": {"best_branch": "rsi/best", "base_ref": "main"}}
EVALS_DIR = os.getenv("EVALS_DIR", "/data/evals")
EVAL_PATH = re.compile(r"evals/([\w-]+)/([\w./-]+\.(?:py|sh|mjs|js))")


def deep_merge(base, patch):
    """매핑은 재귀로 합치고, 목록·값은 통째로 바꿉니다. null은 그 키를 지웁니다."""
    out = dict(base)
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _filled(spec, path):
    cur = spec
    for k in path.split("."):
        if not isinstance(cur, dict) or cur.get(k) in (None, "", []):
            return False
        cur = cur[k]
    return True


def _auto_status(spec, gates):
    """모델이 상태를 안 줬을 때의 추정: 필수 값이 채워져 있으면 '제안', 아니면 '미정'."""
    need = {"target": ["target.goal", "target.editable_paths"], "metric": ["metric.name", "metric.direction"],
            "judge": ["judge.policy"], "budget": ["budget.max_iterations"], "evaluation": ["evaluation.command"],
            "workspace": ["workspace.repo"]}
    st = {k: "proposed" if all(_filled(spec, p) for p in v) else "open" for k, v in need.items()}
    st["gates"] = "proposed" if gates else "open"
    return st


def ordered(spec):
    return {**{k: spec[k] for k in SPEC_ORDER if k in spec}, **{k: v for k, v in spec.items() if k not in SPEC_ORDER}}


def build_board(messages):
    """대화 전체에서 보드 상태를 만듭니다. 전체 Spec YAML 블록이 나오면 그 시점의 기준이 되고, 이후 보드 블록이 그 위에 쌓입니다."""
    spec, text, status, gates, questions, updated, errors = {}, None, {}, [], [], None, []
    for m in messages:
        if m["role"] != "assistant" or not m["text"] or (m.get("meta") or {}).get("streaming"):
            continue
        for lang, body in CODE_BLOCK.findall(m["text"]):
            lang = (lang or "").lower()
            try:
                if lang in ("yaml", "yml") and re.search(r"(?m)^spec:\s*\S", body):
                    data = yaml.safe_load(body)
                    if isinstance(data, dict):
                        spec, text, updated = data, body, m["id"]
                elif lang == "rsi-board":
                    b = yaml.safe_load(body) or {}
                    if not isinstance(b, dict):
                        raise ValueError("매핑이 아님")
                    if isinstance(b.get("spec"), dict):
                        spec, text = deep_merge(spec, b["spec"]), None
                    if isinstance(b.get("status"), dict):
                        status.update({k: v for k, v in b["status"].items() if k in SECTIONS and v in ("decided", "proposed", "open")})
                    if isinstance(b.get("gates"), list):
                        gates = [g if isinstance(g, dict) else {"name": str(g)} for g in b["gates"]]
                    if isinstance(b.get("questions"), list):
                        questions = [str(q) for q in b["questions"]]
                    updated = m["id"]
            except (yaml.YAMLError, ValueError) as e:
                errors.append(f"메시지 {m['id']}: 보드 블록을 읽지 못했습니다 ({str(e).splitlines()[0][:80]})")
    if not spec and not status:
        return None
    full = deep_merge(SPEC_DEFAULTS, spec)
    full["workspace"] = {**SPEC_DEFAULTS["workspace"], **(spec.get("workspace") or {})}
    # 모델이 쓴 전체 YAML은 주석을 살리려고 그대로 쓰되, 기본값을 채워야 했다면 다시 직렬화합니다.
    yaml_text = text if text is not None and full == spec else yaml.safe_dump(ordered(full), sort_keys=False, allow_unicode=True, width=100)
    status = {**_auto_status(full, gates), **status}
    return {"spec": full, "yaml": yaml_text, "status": status, "gates": gates, "questions": questions,
            "errors": specs.validate(full) if spec else ["아직 Spec 값이 없습니다"], "parse_errors": errors, "message_id": updated}


GIT_DETAIL = {"ok": "커밋 있음", "empty": "git이지만 커밋 없음", "none": "git 저장소가 아님", None: "아직 확인 전 (메시지를 보내면 확인)"}
DEFAULT_GITIGNORE = ".env\nnode_modules/\ndist/\nbuild/\n__pycache__/\n*.pyc\n.venv/\nvenv/\n*.log\n"


def readiness(board, scripts, session=None):
    """실행까지 남은 단계: 원본 git → Spec 검사 → 작업 공간 → 평가 스크립트 → 저장 → 잡음 기준."""
    spec = (board or {}).get("spec") or {}
    items = []
    git = (session or {}).get("repo_git")
    items.append({"key": "git", "ok": git == "ok", "label": "원본 git", "detail": GIT_DETAIL.get(git, git), "state": git, "gitignore": DEFAULT_GITIGNORE,
                  "heavy": json.loads((session or {}).get("repo_heavy") or "[]")})
    errs = board["errors"] if board else ["아직 Spec 값이 없습니다"]
    items.append({"key": "valid", "ok": not errs, "label": "Spec 필수 항목", "detail": errs[0] if errs else "검사 통과"})
    repo = (spec.get("workspace") or {}).get("repo")
    ws_ok = bool(repo) and os.path.isdir(os.path.join(repo, ".git"))
    items.append({"key": "workspace", "ok": ws_ok, "label": "작업 공간", "detail": repo or "경로 미정", "repo": repo})
    cmd = (spec.get("evaluation") or {}).get("command") or ""
    m = EVAL_PATH.search(cmd)
    folder = m.group(1) if m else None
    files = []
    if folder:
        names = {s["filename"] for s in scripts} | {m.group(2)}
        files = [{"filename": n, "exists": os.path.isfile(os.path.join(EVALS_DIR, folder, n)), "draft": n in {s["filename"] for s in scripts}}
                 for n in sorted(names)]
    ev_ok = bool(files) and all(f["exists"] for f in files)
    items.append({"key": "scripts", "ok": ev_ok, "label": "평가 스크립트", "folder": folder, "files": files,
                  "detail": f"evals/{folder}/" if folder else "evaluation.command에 evals/<폴더>/<파일> 경로가 없습니다"})
    name = spec.get("spec")
    latest = specs.get_version(name) if name else None
    same = False
    if latest:
        old = dict(yaml.safe_load(latest["yaml"]) or {})
        new = dict(spec)
        old.pop("version", None); new.pop("version", None)
        same = old == new
    items.append({"key": "saved", "ok": same, "label": "Spec 저장", "name": name,
                  "version": latest["version"] if latest else None, "changed": bool(latest) and not same,
                  "detail": f"v{latest['version']} 저장됨" if same else (f"v{latest['version']} 이후 변경됨" if latest else "아직 저장 안 됨")})
    nt = (spec.get("metric") or {}).get("noise_threshold")
    dry = None
    if name:
        with db.connect() as conn:
            r = conn.execute("SELECT id, status FROM runs WHERE spec=? AND dry_run=1 ORDER BY started_at DESC LIMIT 1", (name,)).fetchone()
            dry = dict(r) if r else None
    items.append({"key": "noise", "ok": nt is not None, "label": "잡음 기준", "dry_run": dry,
                  "detail": f"noise_threshold {nt}" if nt is not None else ("측정 런 결과로 기준을 정하세요" if dry else "측정만 런으로 잡음을 재야 합니다")})
    return items


# ---- 실행 요청: 도우미는 읽기 전용이라 ```rsi-actions``` 블록으로 할 일을 제안하고, 사용자가 카드에서 눌러야 실행됩니다.
ACTION_TYPES = {"init_repo", "create_workspace", "save_script", "save_spec", "dry_run"}
DESIGN_CMD_TYPES = ("save_eval_script", "create_workspace", "init_repo", "start_run", "save_spec")


def extract_actions(messages):
    """메시지별 실행 요청 목록. 키는 '메시지ID:순번'이라 같은 제안을 두 번 실행해도 어느 카드인지 구분됩니다."""
    out = {}
    for m in messages:
        if m["role"] != "assistant" or not m["text"] or (m.get("meta") or {}).get("streaming"):
            continue
        acts = []
        for lang, body in CODE_BLOCK.findall(m["text"]):
            if (lang or "").lower() != "rsi-actions":
                continue
            try:
                data = yaml.safe_load(body) or []
            except yaml.YAMLError:
                continue
            for a in data if isinstance(data, list) else []:
                if isinstance(a, dict) and a.get("type") in ACTION_TYPES:
                    acts.append({**a, "key": f"{m['id']}:{len(acts)}"})
        if acts:
            out[m["id"]] = acts
    return out


def action_states(actions, cmds, ready, session):
    """실행한 적 있으면 그 명령의 상태, 아니면 현재 상태로 보아 이미 된 일인지(done) 표시합니다."""
    by_key = {}
    for c in sorted(cmds, key=lambda c: c["id"]):
        k = (json.loads(c["payload"] or "{}")).get("action_key")
        if k:
            by_key[k] = c
    r = {i["key"]: i for i in ready}
    for acts in actions.values():
        for a in acts:
            c = by_key.get(a["key"])
            a["command"] = {"id": c["id"], "status": c["status"], "result": c["result"]} if c else None
            t = a["type"]
            if t == "init_repo":
                a["done"] = session.get("repo_git") == "ok"
            elif t == "create_workspace":
                a["done"] = os.path.isdir(os.path.join("/home/heejo/RSI_Loop/workspaces", str(a.get("name", "")), ".git"))
            elif t == "save_script":
                a["done"] = os.path.isfile(os.path.join(EVALS_DIR, str(a.get("folder", "")), str(a.get("file", ""))))
            elif t == "save_spec":
                a["done"] = r["saved"]["ok"]
            elif t == "dry_run":
                a["done"] = bool(r["noise"]["dry_run"]) or r["noise"]["ok"]
    return actions


def load_session(conn, sid):
    s = conn.execute("SELECT * FROM design_sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        raise HTTPException(404, "설계 세션이 없습니다.")
    msgs = [dict(r) for r in conn.execute("SELECT * FROM design_messages WHERE session_id=? ORDER BY id", (sid,))]
    for m in msgs:
        m["meta"] = json.loads(m["meta"]) if m["meta"] else None
    cmds = [dict(r) for r in conn.execute(
        f"SELECT id, type, status, result, payload, created_at FROM commands WHERE type IN ({','.join('?' * len(DESIGN_CMD_TYPES))}) "
        "AND json_extract(payload, '$.session_id') = ? ORDER BY id DESC LIMIT 30", (*DESIGN_CMD_TYPES, sid))]
    return dict(s), msgs, cmds


def status_context(session, msgs, cmds):
    """다음 메시지와 함께 도우미에게 넘길 대시보드 상태 — 실행 결과를 도우미가 알 수 있게 합니다."""
    _, scripts = extract_drafts(msgs)
    board = build_board(msgs)
    lines = [f"- {i['label']}: {'완료' if i['ok'] else '미완료'} ({i['detail']})" for i in readiness(board, scripts, session) if i["key"] != "git"]
    last_user = max((m["ts"] for m in msgs if m["role"] == "user"), default="")
    recent = [c for c in cmds if c["created_at"] >= last_user and c["type"] != "design_message"]
    for c in sorted(recent, key=lambda c: c["id"]):
        lines.append(f"- 실행 결과 [{c['type']}] {c['status']}: {c['result'] or ''}")
    name = ((board or {}).get("spec") or {}).get("spec")
    if name:
        with db.connect() as conn:
            for r in conn.execute("SELECT id, status, dry_run, baseline_objective, best_objective, ended_at FROM runs WHERE spec=? "
                                  "ORDER BY started_at DESC LIMIT 2", (name,)):
                lines.append(f"- 최근 런 {r['id']} ({'측정만' if r['dry_run'] else '개선'}): {r['status']}"
                             + (f", 기준 {r['baseline_objective']}" if r["baseline_objective"] is not None else "")
                             + (f", 최고 {r['best_objective']}" if r["best_objective"] is not None else "")
                             + (" — run_status로 사유 확인" if r["status"] == "aborted" else ""))
    lines.append("- 너는 준비 작업 도구(init_repo, create_workspace, save_script, save_spec, dry_run)와 런 확인 도구(run_status, run_log, stop_run)를 "
                 "직접 호출할 수 있다. 미완료 단계나 확인할 일은 사용자에게 넘기지 말고 도구로 진행하라.")
    return "[대시보드 상태 — 사용자가 아니라 시스템이 붙인 정보]\n" + "\n".join(lines)


def process_spec_saves():
    """도우미가 save_spec 도구로 넣은 요청을 처리합니다(보드 상태는 대시보드만 계산하므로 여기서). ingest 루프가 주기적으로 부릅니다."""
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT id, payload FROM commands WHERE type='save_spec' AND status='queued'")]
    for r in rows:
        payload = json.loads(r["payload"] or "{}")
        sid = payload.get("session_id")
        try:
            with db.connect() as conn:
                _, msgs, _ = load_session(conn, sid)
            board = build_board(msgs)
            patch = payload.get("spec") or {}
            if not board and not patch:
                raise ValueError("아직 보드에 Spec이 없습니다")
            text = board["yaml"] if board and not patch else yaml.safe_dump(
                ordered(deep_merge((board or {}).get("spec") or SPEC_DEFAULTS, patch)), sort_keys=False, allow_unicode=True, width=100)
            res = specs.save(text, f"설계 세션 #{sid} (도우미)", f"design:{sid}")
            status, result = "applied", f"{res['name']} v{res['version']} 저장"
        except HTTPException as e:
            d = e.detail
            status, result = "rejected", (f"{d['message']}: " + "; ".join(d["errors"])) if isinstance(d, dict) else str(d)
        except Exception as e:
            status, result = "failed", str(e)[:300]
        with db.connect() as conn:
            conn.execute("UPDATE commands SET status=?, result=? WHERE id=?", (status, result[:500], r["id"]))


class NewSession(BaseModel):
    title: str
    repo: str
    message: str


class Message(BaseModel):
    text: str


class SaveScript(BaseModel):
    folder: str
    filename: str
    content: str


class Workspace(BaseModel):
    source: str
    name: str


@router.get("")
def list_sessions():
    with db.connect() as conn:
        rows = conn.execute("SELECT s.*, (SELECT COUNT(*) FROM design_messages m WHERE m.session_id = s.id) AS messages "
                            "FROM design_sessions s ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]


@router.post("")
def create_session(req: NewSession):
    if not req.repo.startswith("/") or ".." in req.repo:
        raise HTTPException(400, "저장소 경로는 절대 경로여야 합니다.")
    if not req.message.strip():
        raise HTTPException(400, "첫 메시지를 입력하세요.")
    with db.connect() as conn:
        cur = conn.execute("INSERT INTO design_sessions(title, repo, claude_session_id, status, created_at, updated_at) "
                           "VALUES (?, ?, ?, 'thinking', ?, ?)", (req.title.strip() or "새 설계", req.repo.rstrip("/"), str(uuid.uuid4()), now(), now()))
        sid = cur.lastrowid
        mid = conn.execute("INSERT INTO design_messages(session_id, role, text, ts) VALUES (?, 'user', ?, ?)",
                           (sid, req.message.strip(), now())).lastrowid
        queue(conn, "design_message", {"session_id": sid, "message_id": mid})
    return {"id": sid}


@router.get("/{sid}")
def get_session(sid: int):
    with db.connect() as conn:
        s, msgs, cmds = load_session(conn, sid)
    spec_draft, scripts = extract_drafts(msgs)
    board = build_board(msgs)
    if s.get("repo_git") != "ok" and s.get("repo_heavy") is None:
        # 원본의 git 상태·큰 폴더를 아직 모르면 오케스트레이터에 한 번 확인을 맡깁니다(읽기만 함).
        with db.connect() as conn:
            if not conn.execute("SELECT 1 FROM commands WHERE type='repo_check' AND status IN ('queued','delivered') "
                                "AND json_extract(payload, '$.session_id') = ?", (sid,)).fetchone():
                queue(conn, "repo_check", {"session_id": sid})
    ready = readiness(board, scripts, s)
    actions = action_states(extract_actions(msgs), cmds, ready, s)
    for m in msgs:
        m["actions"] = actions.get(m["id"])
    return {"session": s, "messages": msgs, "spec_draft": spec_draft, "scripts": scripts, "commands": cmds,
            "board": board, "readiness": ready}


@router.post("/{sid}/actions/{key}")
def run_action(sid: int, key: str):
    """제안된 실행 요청 하나를 실행합니다. 내용은 클라이언트가 아니라 저장된 대화에서 다시 읽습니다."""
    with db.connect() as conn:
        s, msgs, cmds = load_session(conn, sid)
        act = next((a for acts in extract_actions(msgs).values() for a in acts if a["key"] == key), None)
        if not act:
            raise HTTPException(404, "해당 실행 요청을 찾을 수 없습니다.")
        if any(json.loads(c["payload"] or "{}").get("action_key") == key and c["status"] in ("queued", "delivered") for c in cmds):
            raise HTTPException(409, "이미 실행 중입니다.")
        _, scripts = extract_drafts(msgs)
        board = build_board(msgs)
        base = {"session_id": sid, "action_key": key}
        t = act["type"]
        if t == "init_repo":
            queue(conn, "init_repo", {**base, "repo": s["repo"], "gitignore": str(act.get("gitignore") or "")})
        elif t == "create_workspace":
            name = str(act.get("name") or "")
            if not SLUG.match(name):
                raise HTTPException(400, "작업 공간 이름(소문자·숫자·_-)이 올바르지 않습니다.")
            queue(conn, "create_workspace", {**base, "source": s["repo"], "name": name})
        elif t == "save_script":
            folder, fname = str(act.get("folder") or ""), str(act.get("file") or "")
            draft = next((x for x in scripts if x["filename"] == fname), None)
            if not SLUG.match(folder) or not SCRIPT_NAME.match(fname):
                raise HTTPException(400, "폴더·파일 이름이 올바르지 않습니다.")
            if not draft:
                raise HTTPException(400, f"대화에 '# file: {fname}' 초안이 없습니다.")
            queue(conn, "save_eval_script", {**base, "folder": folder, "filename": fname, "content": draft["content"].split("\n", 1)[1] if "\n" in draft["content"] else ""})
        elif t == "save_spec":
            if not board:
                raise HTTPException(400, "아직 보드에 Spec이 없습니다.")
            r = specs.save(board["yaml"], f"설계 세션 #{sid}", f"design:{sid}")
            conn.execute("INSERT INTO commands(run_id, type, payload, status, result, created_at) VALUES (NULL, 'save_spec', ?, 'applied', ?, ?)",
                         (json.dumps(base), f"{r['name']} v{r['version']} 저장", now()))
        elif t == "dry_run":
            name = (board or {}).get("spec", {}).get("spec")
            row = specs.get_version(name) if name else None
            if not row:
                raise HTTPException(400, "먼저 Spec을 저장하세요.")
            n = act.get("iterations") if isinstance(act.get("iterations"), int) and 1 <= act["iterations"] <= 10 else 5
            queue(conn, "start_run", {**base, "spec": name, "version": row["version"], "iterations": n, "dry_run": True})
    return {"ok": True}


class InitRepo(BaseModel):
    gitignore: str = ""


@router.post("/{sid}/init-repo")
def init_repo(sid: int, req: InitRepo):
    """실행 준비 체크리스트에서 원본 git 초기화 — 도우미의 제안 없이도 GUI로 할 수 있게."""
    with db.connect() as conn:
        s, _, cmds = load_session(conn, sid)
        if any(c["type"] == "init_repo" and c["status"] in ("queued", "delivered") for c in cmds):
            raise HTTPException(409, "이미 실행 중입니다.")
        queue(conn, "init_repo", {"session_id": sid, "repo": s["repo"], "gitignore": req.gitignore[:5000]})
    return {"ok": True}


@router.post("/{sid}/save-spec")
def save_board_spec(sid: int):
    """보드의 현재 Spec을 새 버전으로 저장합니다."""
    with db.connect() as conn:
        _, msgs, _ = load_session(conn, sid)
    board = build_board(msgs)
    if not board:
        raise HTTPException(400, "아직 보드에 Spec이 없습니다.")
    return specs.save(board["yaml"], f"설계 세션 #{sid}", f"design:{sid}")


@router.post("/{sid}/messages")
def send_message(sid: int, req: Message):
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "메시지를 입력하세요.")
    with db.connect() as conn:
        s = conn.execute("SELECT status FROM design_sessions WHERE id=?", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "설계 세션이 없습니다.")
        if s["status"] == "thinking":
            raise HTTPException(409, "이전 답변을 기다리는 중입니다.")
        sess, msgs, cmds = load_session(conn, sid)
        meta = {"context": status_context(sess, msgs, cmds)}
        mid = conn.execute("INSERT INTO design_messages(session_id, role, text, meta, ts) VALUES (?, 'user', ?, ?, ?)",
                           (sid, text, json.dumps(meta, ensure_ascii=False), now())).lastrowid
        conn.execute("UPDATE design_sessions SET status='thinking', last_error=NULL, updated_at=? WHERE id=?", (now(), sid))
        queue(conn, "design_message", {"session_id": sid, "message_id": mid})
    return {"ok": True}


@router.post("/{sid}/save-script")
def save_script(sid: int, req: SaveScript):
    if not SLUG.match(req.folder) or not SCRIPT_NAME.match(req.filename):
        raise HTTPException(400, "폴더 이름(소문자·숫자·_-)과 파일 경로(하위 폴더 3단계까지, .py/.sh/.mjs/.js/.json/.txt/.yaml)를 확인하세요.")
    with db.connect() as conn:
        queue(conn, "save_eval_script", {"session_id": sid, "folder": req.folder, "filename": req.filename, "content": req.content})
    return {"ok": True, "path": f"evals/{req.folder}/{req.filename}"}


@router.post("/{sid}/workspace")
def create_workspace(sid: int, req: Workspace):
    if not req.source.startswith("/") or ".." in req.source or not SLUG.match(req.name):
        raise HTTPException(400, "원본 경로(절대 경로)와 작업 공간 이름(소문자·숫자·_-)을 확인하세요.")
    with db.connect() as conn:
        queue(conn, "create_workspace", {"session_id": sid, "source": req.source.rstrip("/"), "name": req.name})
    return {"ok": True, "path": f"/home/heejo/RSI_Loop/workspaces/{req.name}"}
