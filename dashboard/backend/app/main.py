import asyncio
import json
import logging
import os
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from pydantic import BaseModel, Field

import mimetypes

from . import audit, auth, db, designs, ingest, insights, notifier, specs
from .ingest import objective_of

logging.basicConfig(level=logging.INFO)
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "rsi-dev-secret-change-me")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
DEFAULT_REPO = os.getenv("DEFAULT_REPO", "")
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", "/app/frontend/dist"))
mimetypes.add_type("application/manifest+json", ".webmanifest")   # PWA 매니페스트
# 이벤트가 이 시간 이상 없으면 "running" 런을 응답 없음(stale)으로 표시 — 벤치마크 한 번이 수 분 걸리므로 넉넉히.
STALE_AFTER = timedelta(minutes=int(os.getenv("STALE_AFTER_MIN", "30")))
MAX_DIFF_BYTES = 400_000
ORCH_ONLINE_SEC = 15          # 오케스트레이터 heartbeat가 이 시간 안에 있으면 연결됨
ORCH_ALERT_MIN = 2            # 실행 중 런이 있는데 이 시간 이상 끊기면 텔레그램 알림
RUN_COMMANDS = {"stop", "stop_after", "pause", "resume", "inject", "approve", "reject", "set_roles"}
# Phase 0 초기 런에는 지표 정보가 없어서 당시 지표로 채웁니다.
LEGACY_METRIC = {"name": "S1 세션 총 대기시간", "unit": "ms", "direction": "minimize"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    audit.prune()
    specs.import_files()
    ingest.rebuild_runs()
    tasks = [asyncio.create_task(ingest.ingest_loop()), asyncio.create_task(orchestrator_watchdog())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="RSI Loop Dashboard", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    session_cookie="rsi_session",
    same_site="lax",
    https_only=COOKIE_SECURE,
    max_age=auth.TRUSTED_DAYS * 24 * 3600,  # 실제 만료는 세션 안의 exp로 판단(신뢰 기기 여부에 따라 다름)
)
app.add_middleware(audit.AuditMiddleware)
app.include_router(auth.router)
app.include_router(audit.router)
app.include_router(specs.router)
app.include_router(designs.router)
app.include_router(insights.router)
admin = [Depends(auth.require_admin)]


# ---------------------------------------------------------------- 요약 계산

def _run_status(row):
    status = row["status"]
    if status == "running" and row["last_event_at"]:
        if datetime.now() - datetime.fromisoformat(row["last_event_at"]) > STALE_AFTER:
            return "stale"
    return status


def _phase(it):
    if it.get("decision"):
        return "done"
    if it.get("approval_pending"):
        return "approval"
    if it.get("active_role") in ("planner", "reviewer"):
        return it["active_role"]
    if it.get("objective") is not None:
        return "judging"
    if any(g["gate"] == "build" for g in it["gates"]) or it.get("eval_stages") or it.get("live"):
        return "benchmarking"
    if it.get("agent_done"):
        return "building"
    return "agent"


def summarize_iterations(conn, run_id):
    its = {}
    rows = conn.execute("SELECT type, data FROM events WHERE run_id=? AND iteration IS NOT NULL ORDER BY seq", (run_id,))
    for r in rows:
        d = json.loads(r["data"])
        n = d["iteration"]
        it = its.setdefault(n, {"iteration": n, "gates": [], "tool_calls": 0, "files_touched": [], "stages": []})
        t = r["type"]
        if t == "iteration_start":
            it.update(started_at=d["ts"], branch=d.get("branch"))
        elif t == "agent_tool":
            if d.get("role", "implementer") == "implementer":
                it["tool_calls"] += 1
            it["last_tool"] = (f"[{d['role']}] " if d.get("role") and d["role"] != "implementer" else "") + \
                f"{d.get('tool')} {Path(d.get('target') or '').name}".strip()
        elif t == "role_start":
            it["active_role"] = d.get("role")
            it["stages"].append({"role": d.get("role"), "tier": d.get("tier"), "model": d.get("model"), "started_at": d["ts"]})
        elif t == "role_done":
            it["active_role"] = None
            st = next((s for s in reversed(it["stages"]) if s["role"] == d.get("role") and "ok" not in s), None)
            if st is not None:
                st.update({k: d.get(k) for k in ("ok", "error", "duration_ms", "wtok", "usage", "util_before", "util_after", "model", "tool_calls")})
        elif t == "model_fallback":
            it.setdefault("warnings", []).append(f"{d.get('role')}: 모델 {d.get('model')} 사용 불가 → 기본 모델로 재실행")
        elif t == "hypotheses":
            it["hypotheses"] = {"reason": d.get("reason"), "items": d.get("items"), "ok": d.get("ok")}
        elif t == "hypothesis_assigned":
            it["assigned"] = d.get("title")
        elif t == "review":
            it["review"] = {k: d.get(k) for k in ("verdict", "risk", "reasons", "ok")}
        elif t == "agent_done":
            it.update(agent_done=True, summary=d.get("summary"), hypothesis=d.get("hypothesis"),
                      agent_duration_ms=d.get("duration_ms"), num_turns=d.get("num_turns"),
                      output_tokens=d.get("output_tokens"), agent_timed_out=d.get("timed_out"))
        elif t == "candidate_commit":
            it.update(commit=d.get("commit"), files_touched=d.get("files", []), diff_stat=d.get("diff_stat"))
        elif t == "gate":
            it["gates"].append({"gate": d.get("gate"), "passed": d.get("passed"), "detail": d.get("detail") or d.get("log")})
        elif t == "metric":
            it.update(objective=objective_of(d), breakdown=d.get("breakdown") or d.get("groups_ms"),
                      best_before=d.get("best_objective"))
        elif t == "approval_required":
            it.update(approval_pending=True, approval={k: d.get(k) for k in ("reason", "improvement", "diff_lines", "commit")})
        elif t == "approval":
            it.update(approval_pending=False, approved=d.get("approved"))
        elif t == "judgment":
            it.update(decision=d.get("decision"), improvement=d.get("improvement", d.get("improvement_ms")),
                      failed_gate=d.get("failed_gate"), ended_at=d["ts"])
        elif t == "usage":
            it["usage"] = d.get("windows")
        elif t == "stage_result":
            it.setdefault("eval_stages", []).append({k: d.get(k) for k in (
                "stage", "index", "of", "names", "objective", "passed", "early_stopped", "seconds", "curve", "best_curve", "best_objective")})
            it.get("live", {}).pop(d.get("stage"), None)
        elif t == "eval_progress":
            # 진행 중인 단계의 학습 곡선(20초 간격 표본) — 단계가 끝나면 stage_result의 곡선으로 대체
            it.setdefault("live", {}).setdefault(d.get("stage"), []).append({"step": d.get("step"), "value": d.get("value"), "total": d.get("total")})
    # 진행 중 곡선 옆에 그릴 best 곡선: 이 런의 기준 측정(반복 밖) 또는 앞선 반복의 단계 결과에서
    best_curves = {}
    for r in conn.execute("SELECT data FROM events WHERE run_id=? AND type='stage_result' ORDER BY seq", (run_id,)):
        d = json.loads(r["data"])
        if d.get("label") == "best" and d.get("curve"):
            best_curves[d["stage"]] = d["curve"]
        elif d.get("best_curve"):
            best_curves.setdefault(d["stage"], d["best_curve"])
    for it in its.values():
        it["phase"] = _phase(it)
        if it.get("live"):
            it["live"] = [{"stage": k, "curve": v, "best_curve": best_curves.get(k) or []} for k, v in it["live"].items()]
    return [its[k] for k in sorted(its)]


def _mark_interrupted(its, status):
    # 런이 중단·응답 없음이면 끝나지 못한 반복은 진행 중이 아니라 중단된 것으로 표시합니다.
    if status in ("aborted", "stale"):
        for it in its:
            if it["phase"] != "done":
                it["phase"] = "interrupted"
    return its


def latest_usage(conn):
    """루프 이벤트의 사용량과, 설계 대화 중 오케스트레이터가 남긴 사용량(settings.usage_latest) 중 최신 값."""
    row = conn.execute("SELECT ts, data FROM events WHERE type='usage' ORDER BY ts DESC LIMIT 1").fetchone()
    loop = None
    if row:
        d = json.loads(row["data"])
        loop = {"ts": row["ts"], "windows": d.get("windows"), "status": d.get("status")}
    design = db.get_setting("usage_latest")
    candidates = [u for u in (loop, design) if u]
    best = max(candidates, key=lambda u: u["ts"]) if candidates else None
    s = conn.execute("SELECT five_reset, seven_reset FROM usage_samples WHERE five_reset IS NOT NULL ORDER BY ts DESC LIMIT 1").fetchone()
    if best and s:
        best = {**best, "resets": {"five_hour": s["five_reset"], "seven_day": s["seven_reset"]}}
    return best


def run_summary(conn, row):
    its = _mark_interrupted(summarize_iterations(conn, row["id"]), _run_status(row))
    decisions = [i.get("decision") for i in its if i.get("decision")]
    current = next((i for i in reversed(its) if i["phase"] not in ("done", "interrupted")), None)
    status = _run_status(row)
    pending = next((i for i in its if i["phase"] == "approval"), None)
    return {
        **{k: row[k] for k in row.keys()},
        "metric": json.loads(row["metric"]) if row["metric"] else LEGACY_METRIC,
        "dry_run": bool(row["dry_run"]),
        "pending_approval": ({"iteration": pending["iteration"], **pending["approval"]} if pending and status == "running" else None),
        # 목록의 스파크라인·판정 점용 요약 (반복별 측정값과 판정)
        "series": [{"i": i["iteration"], "v": i.get("objective"),
                    "d": i.get("decision") or ("interrupted" if i["phase"] == "interrupted" else None)} for i in its],
        "status": status,
        "iterations_done": len(decisions),
        "accepted": decisions.count("accepted"),
        "current": ({"iteration": current["iteration"], "phase": current["phase"], "last_tool": current.get("last_tool")}
                    if current and status in ("running", "paused")
                    # 첫 반복 전: 현재 best를 기준으로 측정하는 중(수 분)
                    else {"iteration": 0, "phase": "baseline", "last_tool": None}
                    if status == "running" and not its and row["baseline_objective"] is None else None),
    }


# ---------------------------------------------------------------- API

@app.get("/api/overview", dependencies=admin)
def overview():
    with db.connect() as conn:
        runs = [run_summary(conn, r) for r in conn.execute("SELECT * FROM runs ORDER BY started_at DESC")]
        usage = latest_usage(conn)
        recent = [json.loads(r["data"]) for r in
                  conn.execute("SELECT data FROM events WHERE type IN ('judgment','paused','run_start','run_end','run_aborted') "
                               "ORDER BY ts DESC LIMIT 12")]
    return {"usage": usage, "runs": runs, "recent": recent, "orchestrator": orchestrator_status(),
            "pending_approvals": sum(1 for r in runs if r["pending_approval"]),
            "active": [r for r in runs if r["status"] in ("running", "paused")]}


@app.get("/api/runs/{run_id}", dependencies=admin)
def run_detail(run_id: str):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, "런을 찾을 수 없습니다.")
        summary = run_summary(conn, row)
        its = _mark_interrupted(summarize_iterations(conn, run_id), summary["status"])
        alloc = run_allocation(conn, run_id)
        last_seq = conn.execute("SELECT MAX(seq) FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
    return {"run": summary, "iterations": its, "allocation": alloc, "last_seq": last_seq}


def run_allocation(conn, run_id):
    """런 단위 역할·할당 정보 — 시작 설정, 변경 이력(사용자·자동), 역할별 사용량 추정, 조기 종료 권고."""
    out = {"roles": None, "allocation": None, "changes": [], "role_usage": None, "stop_recommended": None, "role_wtok": {}}
    rows = conn.execute("SELECT type, data FROM events WHERE run_id=? AND type IN "
                        "('run_start','roles_changed','role_usage','stop_recommended','role_done') ORDER BY seq", (run_id,))
    for r in rows:
        d = json.loads(r["data"])
        if r["type"] == "run_start":
            out["roles"], out["allocation"] = d.get("roles"), d.get("allocation")
        elif r["type"] == "roles_changed":
            out["changes"].append({k: d.get(k) for k in ("ts", "iteration", "by", "changes", "reason", "command_id")})
        elif r["type"] == "role_usage":
            out["role_usage"] = {"total_util_pct": d.get("total_util_pct"), "per_role": d.get("per_role")}
        elif r["type"] == "stop_recommended":
            out["stop_recommended"] = {"iteration": d.get("iteration"), "reason": d.get("reason")}
        elif r["type"] == "role_done":
            # 런이 진행 중일 때도 역할별 비중을 볼 수 있도록 가중 토큰을 실시간 합산합니다.
            out["role_wtok"][d.get("role")] = round(out["role_wtok"].get(d.get("role"), 0) + (d.get("wtok") or 0), 1)
    return out


@app.get("/api/runs/{run_id}/events", dependencies=admin)
def run_events(run_id: str, after: int = 0, limit: int = 500):
    with db.connect() as conn:
        rows = conn.execute("SELECT data FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                            (run_id, after, min(limit, 2000))).fetchall()
    return [json.loads(r["data"]) for r in rows]


@app.get("/api/runs/{run_id}/iterations/{n}/diff", dependencies=admin)
def iteration_diff(run_id: str, n: int):
    with db.connect() as conn:
        run = conn.execute("SELECT repo FROM runs WHERE id=?", (run_id,)).fetchone()
        ev = conn.execute("SELECT data FROM events WHERE run_id=? AND iteration=? AND type='candidate_commit' "
                          "ORDER BY seq DESC LIMIT 1", (run_id, n)).fetchone()
    if not run or not ev:
        raise HTTPException(404, "이 반복에는 커밋된 변경이 없습니다.")
    repo = run["repo"] or DEFAULT_REPO
    commit = json.loads(ev["data"])["commit"]
    p = subprocess.run(["git", "-c", "safe.directory=*", "-C", repo, "show", "--stat", "--patch", "--format=%H%n%s%n", commit],
                       capture_output=True, text=True, timeout=20)
    if p.returncode != 0:
        raise HTTPException(404, f"커밋을 읽을 수 없습니다: {p.stderr.strip()[:200]}")
    text = p.stdout
    truncated = len(text) > MAX_DIFF_BYTES
    return {"commit": commit, "diff": text[:MAX_DIFF_BYTES], "truncated": truncated}


@app.get("/api/stream", dependencies=admin)
async def stream(request: Request):
    """새 이벤트를 SSE로 밀어줍니다. Cloudflare 유휴 연결 끊김을 막기 위해 20초마다 heartbeat를 보냅니다."""
    q = ingest.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            ingest.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- 제어 (명령 큐 · 오케스트레이터)

class CommandRequest(BaseModel):
    type: str
    run_id: str | None = None
    payload: dict = Field(default_factory=dict)


def orchestrator_status():
    with db.connect() as conn:
        row = conn.execute("SELECT heartbeat_at, state FROM orchestrator WHERE name='main'").fetchone()
    if not row:
        return {"online": False, "heartbeat_at": None, "state": {}}
    age = (datetime.now() - datetime.fromisoformat(row["heartbeat_at"])).total_seconds()
    return {"online": age <= ORCH_ONLINE_SEC, "heartbeat_at": row["heartbeat_at"], "age_sec": round(age), "state": json.loads(row["state"] or "{}")}


async def orchestrator_watchdog():
    alerted = False
    while True:
        await asyncio.sleep(30)
        try:
            st = orchestrator_status()
            with db.connect() as conn:
                running = conn.execute("SELECT COUNT(*) FROM runs WHERE status IN ('running','paused')").fetchone()[0]
            lost_min = (st.get("age_sec") or 0) / 60
            if st["heartbeat_at"] and running and lost_min >= ORCH_ALERT_MIN and not alerted:
                notifier.on_orchestrator_lost(int(lost_min))
                alerted = True
            elif st["online"]:
                alerted = False
        except Exception:
            logging.exception("watchdog error")


@app.get("/api/orchestrator", dependencies=admin)
def get_orchestrator():
    return orchestrator_status()


@app.post("/api/commands", dependencies=admin)
def create_command(req: CommandRequest):
    payload = dict(req.payload)
    if req.type == "start_run":
        row = specs.get_version(payload.get("spec"), payload.get("version"))
        if not row:
            raise HTTPException(400, "알 수 없는 Spec 또는 버전입니다.")
        payload["version"] = row["version"]
        spec_data = __import__("yaml").safe_load(row["yaml"])
        if spec_data["metric"].get("noise_threshold") is None and not payload.get("dry_run"):
            raise HTTPException(400, "이 Spec 버전에는 채택 기준(metric.noise_threshold)이 없습니다. 먼저 검증(측정만) 런으로 잡음을 재세요.")
        iterations = int(payload.get("iterations") or 0)
        if not 1 <= iterations <= 50:
            raise HTTPException(400, "반복 횟수는 1~50 사이여야 합니다.")
        payload["iterations"] = iterations
        payload["dry_run"] = bool(payload.get("dry_run"))
        errs = specs.validate_roles(payload.get("roles"), payload.get("allocation"))
        if errs:
            raise HTTPException(400, "; ".join(errs))
    elif req.type in RUN_COMMANDS:
        if not req.run_id:
            raise HTTPException(400, "run_id가 필요합니다.")
        with db.connect() as conn:
            row = conn.execute("SELECT status FROM runs WHERE id=?", (req.run_id,)).fetchone()
        if not row or row["status"] not in ("running", "paused"):
            raise HTTPException(409, "실행 중인 런이 아닙니다.")
        if req.type == "set_roles":
            errs = specs.validate_roles(payload.get("roles"), None) if payload.get("roles") else ["roles가 필요합니다"]
            if errs:
                raise HTTPException(400, "; ".join(errs))
            payload = {"roles": payload["roles"]}
        if req.type in ("approve", "reject"):
            if not isinstance(payload.get("iteration"), int):
                raise HTTPException(400, "iteration이 필요합니다.")
            payload = {"iteration": payload["iteration"]}
        if req.type == "inject":
            text = (payload.get("text") or "").strip()
            if not text or len(text) > 2000:
                raise HTTPException(400, "추가 지시는 1~2000자여야 합니다.")
            payload = {"text": text}
    else:
        raise HTTPException(400, "지원하지 않는 명령입니다.")
    with db.connect() as conn:
        cur = conn.execute("INSERT INTO commands(run_id, type, payload, status, created_at) VALUES (?, ?, ?, 'queued', ?)",
                           (req.run_id, req.type, json.dumps(payload, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")))
        cid = cur.lastrowid
    ingest._broadcast({"type": "command", "id": cid, "run": req.run_id})
    return {"id": cid}


@app.get("/api/commands", dependencies=admin)
def list_commands(run_id: str | None = None, limit: int = 30):
    with db.connect() as conn:
        if run_id:
            rows = conn.execute("SELECT * FROM commands WHERE run_id=? ORDER BY id DESC LIMIT ?", (run_id, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM commands ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{**dict(r), "payload": json.loads(r["payload"] or "{}")} for r in rows]


# ---------------------------------------------------------------- 설정 (텔레그램)

class TelegramSettings(BaseModel):
    chat_id: int | str | None = None
    events: dict | None = None


@app.get("/api/settings/telegram", dependencies=admin)
def get_telegram():
    return {**notifier.config(), "event_types": notifier.EVENT_TYPES}


@app.post("/api/settings/telegram", dependencies=admin)
def set_telegram(req: TelegramSettings):
    if req.chat_id is not None:
        db.set_setting("telegram_chat_id", str(req.chat_id).strip() or None)
    if req.events is not None:
        db.set_setting("telegram_events", {k: bool(v) for k, v in req.events.items() if k in notifier.EVENT_TYPES})
    return get_telegram()


@app.post("/api/settings/telegram/discover", dependencies=admin)
def discover_telegram():
    try:
        return notifier.discover_chats()
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/settings/telegram/test", dependencies=admin)
def test_telegram():
    try:
        notifier.send("🔔 RSI Loop 대시보드 테스트 알림입니다." + (f'\n<a href="{notifier.PUBLIC_URL}/">대시보드 열기</a>' if notifier.PUBLIC_URL else ""))
    except Exception as e:
        raise HTTPException(400, f"전송 실패: {e}")
    return {"ok": True}


class ModelSettings(BaseModel):
    model_tiers: dict | None = None
    tier_factor: dict | None = None
    design_tier: str | None = None


@app.get("/api/settings/models", dependencies=admin)
def get_models():
    return {"model_tiers": {**{"high": "opus", "standard": "sonnet", "light": "haiku"}, **(db.get_setting("model_tiers") or {})},
            "tier_factor": {**{"high": 5.0, "standard": 1.0, "light": 0.25}, **(db.get_setting("tier_factor") or {})},
            "design_tier": db.get_setting("design_tier") or "standard"}


@app.post("/api/settings/models", dependencies=admin)
def set_models(req: ModelSettings):
    if req.model_tiers is not None:
        tiers = {k: (str(v).strip() or None) for k, v in req.model_tiers.items() if k in ("high", "standard", "light")}
        db.set_setting("model_tiers", tiers)
    if req.design_tier in ("high", "standard", "light"):
        db.set_setting("design_tier", req.design_tier)
    if req.tier_factor is not None:
        db.set_setting("tier_factor", {k: float(v) for k, v in req.tier_factor.items() if k in ("high", "standard", "light")})
    return get_models()


@app.get("/api/health")
def health():
    return {"ok": True}


# ---------------------------------------------------------------- 프론트엔드 (SPA)

if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def spa(path: str):
    if path.startswith(("api/", "auth/")):
        raise HTTPException(404)
    # index.html과 서비스 워커는 항상 최신을 받도록 캐시하지 않습니다(배포 직후 예전 화면이 남지 않게).
    no_cache = {"Cache-Control": "no-cache"}
    target = (FRONTEND_DIST / path).resolve()
    if path and target.is_file() and target.is_relative_to(FRONTEND_DIST.resolve()):
        return FileResponse(target, headers=no_cache if path in ("sw.js", "manifest.webmanifest") else None)
    index = FRONTEND_DIST / "index.html"
    if not index.exists():
        raise HTTPException(503, "frontend not built")
    return FileResponse(index, headers=no_cache)
