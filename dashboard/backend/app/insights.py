"""승인함 · 사용량 · 일정 API.

- 승인함: 실행 중인 모든 런의 사람 승인 대기를 한곳에 모읍니다(텔레그램 링크로 들어왔을 때 바로 처리).
- 사용량: usage_samples 시계열 + 런별·역할별 소비 + 효율(채택 1건당 사용량).
- 일정: 요일·시간 창 안에서만 런을 자동 시작합니다. 실제 시작·종료는 오케스트레이터가 합니다.
"""
import json
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import auth, db, specs

router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_admin)])
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


# ---------------------------------------------------------------- 승인함

@router.get("/approvals")
def approvals():
    from .main import run_summary, summarize_iterations   # 순환 import 방지
    pending, recent = [], []
    with db.connect() as conn:
        for row in conn.execute("SELECT * FROM runs WHERE status IN ('running','paused')"):
            s = run_summary(conn, row)
            if s["pending_approval"]:
                it = next((i for i in summarize_iterations(conn, row["id"]) if i["iteration"] == s["pending_approval"]["iteration"]), {})
                pending.append({"run_id": row["id"], "title": row["title"] or row["spec"], "metric": s["metric"],
                                "best_objective": row["best_objective"], **s["pending_approval"],
                                "objective": it.get("objective"), "summary": it.get("summary"), "assigned": it.get("assigned"),
                                "review": it.get("review"), "diff_stat": it.get("diff_stat"), "files": it.get("files_touched")})
        for r in conn.execute("SELECT e.run_id, e.ts, e.data, r.title, r.spec FROM events e JOIN runs r ON r.id = e.run_id "
                              "WHERE e.type='approval' ORDER BY e.ts DESC LIMIT 20"):
            d = json.loads(r["data"])
            recent.append({"run_id": r["run_id"], "title": r["title"] or r["spec"], "iteration": d.get("iteration"),
                           "approved": d.get("approved"), "ts": r["ts"]})
    return {"pending": pending, "recent": recent}


# ---------------------------------------------------------------- 사용량

@router.get("/usage")
def usage(hours: int = 24):
    hours = max(1, min(hours, 24 * 14))
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    with db.connect() as conn:
        samples = [dict(r) for r in conn.execute(
            "SELECT ts, five_hour, seven_day, source, run_id FROM usage_samples WHERE ts >= ? ORDER BY ts", (since,))]
        latest = conn.execute("SELECT * FROM usage_samples ORDER BY ts DESC LIMIT 1").fetchone()
        runs = []
        role_totals = {}
        for r in conn.execute("SELECT id, title, spec, started_at, status, dry_run, baseline_objective, best_objective, metric "
                              "FROM runs ORDER BY started_at DESC LIMIT 30"):
            ru = conn.execute("SELECT data FROM events WHERE run_id=? AND type='role_usage' ORDER BY seq DESC LIMIT 1", (r["id"],)).fetchone()
            accepted = conn.execute("SELECT COUNT(*) FROM events WHERE run_id=? AND type='judgment' AND data LIKE '%\"accepted\"%'",
                                    (r["id"],)).fetchone()[0]
            iterations = conn.execute("SELECT COUNT(*) FROM events WHERE run_id=? AND type='judgment'", (r["id"],)).fetchone()[0]
            util = None
            if ru:
                d = json.loads(ru["data"])
                util = d.get("total_util_pct")
                for role, v in (d.get("per_role") or {}).items():
                    role_totals[role] = round(role_totals.get(role, 0) + (v.get("util_pct") or 0), 2)
            else:
                util = _run_util_from_samples(conn, r["id"])
            if r["dry_run"] or not iterations:
                continue
            runs.append({"id": r["id"], "title": r["title"] or r["spec"], "started_at": r["started_at"], "status": r["status"],
                         "util_pct": util, "iterations": iterations, "accepted": accepted,
                         "baseline": r["baseline_objective"], "best": r["best_objective"],
                         "metric": json.loads(r["metric"]) if r["metric"] else None})
    measured = [x for x in runs if x["util_pct"] is not None]
    total_util = round(sum(x["util_pct"] for x in measured), 2)
    total_acc = sum(x["accepted"] for x in measured)
    total_it = sum(x["iterations"] for x in measured)
    return {
        "samples": samples,
        "current": dict(latest) if latest else None,
        "runs": runs,
        "role_totals": role_totals,
        "efficiency": {"util_per_accept": round(total_util / total_acc, 2) if total_acc else None,
                       "util_per_iteration": round(total_util / total_it, 2) if total_it else None,
                       "accepted": total_acc, "iterations": total_it, "util_pct": total_util},
    }


def _run_util_from_samples(conn, run_id):
    """role_usage가 없는 예전 런: 그 런의 사용량 기록에서 양수 증가분만 더합니다(창 리셋은 0 처리)."""
    vals = [r["five_hour"] for r in conn.execute("SELECT five_hour FROM usage_samples WHERE run_id=? ORDER BY ts", (run_id,))
            if r["five_hour"] is not None]
    if len(vals) < 2:
        return None
    return round(sum(max(0, b - a) for a, b in zip(vals, vals[1:]) if b - a > -0.05) * 100, 2)


# ---------------------------------------------------------------- 일정

class Schedule(BaseModel):
    id: int | None = None
    name: str | None = None
    spec: str
    version: int | None = None       # 비우면 실행 시점의 최신 버전
    iterations: int
    dry_run: bool = False
    days: list[int]                  # 0=월 … 6=일
    start_time: str
    end_time: str
    enabled: bool = True


def window_for(sched, now=None):
    """지금이 이 일정의 시간 창 안이면 (창 시작, 창 끝)을, 아니면 None을 돌려줍니다. 끝이 시작보다 이르면 자정을 넘는 창."""
    now = now or datetime.now()
    days = json.loads(sched["days"]) if isinstance(sched["days"], str) else sched["days"]
    sh, sm = map(int, sched["start_time"].split(":"))
    eh, em = map(int, sched["end_time"].split(":"))
    for back in (0, 1):
        day = (now - timedelta(days=back)).replace(hour=sh, minute=sm, second=0, microsecond=0)
        end = day.replace(hour=eh, minute=em)
        if end <= day:
            end += timedelta(days=1)
        if day.weekday() in days and day <= now < end:
            return day, end
    return None


def next_start(sched, now=None):
    now = now or datetime.now()
    days = json.loads(sched["days"]) if isinstance(sched["days"], str) else sched["days"]
    sh, sm = map(int, sched["start_time"].split(":"))
    for d in range(0, 8):
        cand = (now + timedelta(days=d)).replace(hour=sh, minute=sm, second=0, microsecond=0)
        if cand > now and cand.weekday() in days:
            return cand.isoformat(timespec="minutes")
    return None


@router.get("/schedules")
def list_schedules():
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM schedules ORDER BY id")]
    for r in rows:
        r["days"] = json.loads(r["days"])
        r["enabled"] = bool(r["enabled"])
        r["dry_run"] = bool(r["dry_run"])
        w = window_for(r)
        r["in_window"] = bool(w)
        r["window_end"] = w[1].isoformat(timespec="minutes") if w else None
        r["next_start"] = next_start(r)
    return rows


@router.post("/schedules")
def save_schedule(req: Schedule):
    row = specs.get_version(req.spec, req.version)
    if not row:
        raise HTTPException(400, "알 수 없는 Spec 또는 버전입니다.")
    import yaml
    if not req.dry_run and (yaml.safe_load(row["yaml"]).get("metric") or {}).get("noise_threshold") is None:
        raise HTTPException(400, "이 Spec 버전에는 채택 기준(noise_threshold)이 없어 '측정만'으로만 예약할 수 있습니다.")
    if not (TIME_RE.match(req.start_time) and TIME_RE.match(req.end_time)) or req.start_time == req.end_time:
        raise HTTPException(400, "시간은 HH:MM 형식이고, 시작과 끝이 달라야 합니다.")
    if not req.days or any(d not in range(7) for d in req.days):
        raise HTTPException(400, "요일을 하나 이상 고르세요.")
    if not 1 <= req.iterations <= 50:
        raise HTTPException(400, "반복 횟수는 1~50입니다.")
    vals = (req.name or f"{req.spec} 일정", req.spec, req.version, req.iterations, int(req.dry_run), json.dumps(sorted(set(req.days))),
            req.start_time, req.end_time, int(req.enabled))
    with db.connect() as conn:
        if req.id:
            conn.execute("UPDATE schedules SET name=?, spec=?, version=?, iterations=?, dry_run=?, days=?, start_time=?, end_time=?, "
                         "enabled=? WHERE id=?", (*vals, req.id))
            sid = req.id
        else:
            sid = conn.execute("INSERT INTO schedules(name, spec, version, iterations, dry_run, days, start_time, end_time, enabled, created_at) "
                               "VALUES (?,?,?,?,?,?,?,?,?,?)", (*vals, datetime.now().isoformat(timespec="seconds"))).lastrowid
    return {"id": sid}


@router.delete("/schedules/{sid}")
def delete_schedule(sid: int):
    with db.connect() as conn:
        conn.execute("DELETE FROM schedules WHERE id=?", (sid,))
    return {"ok": True}


# ---------------------------------------------------------------- 런 관리 · 비교

@router.get("/runs")
def list_runs(spec: str | None = None, status: str | None = None, archived: str = "0"):
    from .main import run_summary
    with db.connect() as conn:
        meta = {r["run_id"]: dict(r) for r in conn.execute("SELECT * FROM run_meta")}
        out = []
        for row in conn.execute("SELECT * FROM runs ORDER BY started_at DESC"):
            m = meta.get(row["id"], {})
            is_arch = bool(m.get("archived"))
            if (archived == "0" and is_arch) or (archived == "1" and not is_arch):
                continue
            if spec and row["spec"] != spec:
                continue
            s = run_summary(conn, row)
            # 상태 필터: running(일시정지 포함) · finished · aborted(응답 없음 포함) · dry(측정만)
            kind = "dry" if s["dry_run"] else {"paused": "running", "stale": "aborted"}.get(s["status"], s["status"])
            if status and status != kind:
                continue
            out.append({**s, "archived": is_arch, "note": m.get("note")})
    return out


class RunMeta(BaseModel):
    archived: bool | None = None
    note: str | None = None


@router.post("/runs/{run_id}/meta")
def set_run_meta(run_id: str, req: RunMeta):
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
            raise HTTPException(404, "런을 찾을 수 없습니다.")
        conn.execute("INSERT INTO run_meta(run_id, archived, note, updated_at) VALUES (?, COALESCE(?, 0), ?, ?) "
                     "ON CONFLICT(run_id) DO UPDATE SET archived=COALESCE(?, archived), note=COALESCE(?, note), updated_at=excluded.updated_at",
                     (run_id, None if req.archived is None else int(req.archived), req.note, datetime.now().isoformat(timespec="seconds"),
                      None if req.archived is None else int(req.archived), req.note))
    return {"ok": True}


@router.delete("/runs/{run_id}")
def delete_run(run_id: str):
    """DB에서 런 기록을 지우고, 오케스트레이터에게 런 폴더를 휴지통(runs/_trash)으로 옮기라고 요청합니다."""
    with db.connect() as conn:
        row = conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, "런을 찾을 수 없습니다.")
        if row["status"] in ("running", "paused"):
            raise HTTPException(409, "실행 중인 런은 삭제할 수 없습니다. 먼저 중단하세요.")
        conn.execute("INSERT INTO commands(run_id, type, payload, status, created_at) VALUES (?, 'delete_run', ?, 'queued', ?)",
                     (run_id, json.dumps({"run_id": run_id}), datetime.now().isoformat(timespec="seconds")))
        for table, col in (("events", "run_id"), ("runs", "id"), ("run_meta", "run_id"), ("usage_samples", "run_id")):
            conn.execute(f"DELETE FROM {table} WHERE {col}=?", (run_id,))
    return {"ok": True}


@router.get("/compare")
def compare(ids: str):
    from .main import run_allocation, run_summary, summarize_iterations
    run_ids = [i for i in ids.split(",") if i][:3]
    out = []
    with db.connect() as conn:
        for rid in run_ids:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
            if not row:
                continue
            s = run_summary(conn, row)
            alloc = run_allocation(conn, rid)
            util = (alloc.get("role_usage") or {}).get("total_util_pct")
            if util is None:
                util = _run_util_from_samples(conn, rid)
            its = summarize_iterations(conn, rid)
            out.append({**{k: s[k] for k in ("id", "title", "spec", "spec_version", "status", "metric", "baseline_objective",
                                             "best_objective", "iterations_done", "accepted", "series", "dry_run", "started_at", "judge")},
                        "util_pct": util, "roles": alloc.get("roles"), "role_usage": alloc.get("role_usage"),
                        "durations_min": round(sum(((datetime.fromisoformat(i["ended_at"]) - datetime.fromisoformat(i["started_at"])).total_seconds()
                                                    for i in its if i.get("ended_at") and i.get("started_at")), 0) / 60, 1)})
    return out


# ---------------------------------------------------------------- 실행 노드

NODE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


class Node(BaseModel):
    id: str
    name: str | None = None
    transport: str = "ssh"            # local | ssh
    host: str | None = None
    port: int = 22
    user: str | None = None
    shell: str = "bash"               # bash | powershell
    os: str = "linux"                 # linux | windows
    tags: list[str] = []
    workdir: str | None = None
    enabled: bool = True


@router.get("/nodes")
def list_nodes():
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM nodes ORDER BY transport = 'local' DESC, id")]
    for r in rows:
        r["tags"] = json.loads(r["tags"] or "[]")
        r["info"] = json.loads(r["info"] or "{}")
        r["enabled"] = bool(r["enabled"])
        r["online"] = None if r["online"] is None else bool(r["online"])
    return {"nodes": rows, "public_key": db.get_setting("node_ssh_pubkey")}


@router.post("/nodes")
def save_node(req: Node):
    if not NODE_ID.match(req.id):
        raise HTTPException(400, "노드 ID는 소문자·숫자·하이픈 (예: win-gpu)")
    if req.transport not in ("local", "ssh") or req.shell not in ("bash", "powershell", "cmd") or req.os not in ("linux", "windows"):
        raise HTTPException(400, "transport/shell/os 값을 확인하세요.")
    if req.transport == "ssh" and not req.host:
        raise HTTPException(400, "SSH 노드는 호스트(IP)가 필요합니다.")
    tags = sorted({t.strip().lower() for t in req.tags if t.strip()})
    with db.connect() as conn:
        conn.execute("""INSERT INTO nodes(id, name, transport, host, port, user, shell, os, tags, workdir, enabled, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET name=excluded.name, transport=excluded.transport, host=excluded.host,
                        port=excluded.port, user=excluded.user, shell=excluded.shell, os=excluded.os, tags=excluded.tags,
                        workdir=excluded.workdir, enabled=excluded.enabled""",
                     (req.id, req.name or req.id, req.transport, req.host, req.port, req.user, req.shell, req.os,
                      json.dumps(tags), req.workdir, int(req.enabled), datetime.now().isoformat(timespec="seconds")))
        # 저장 직후 연결 확인을 요청합니다(오케스트레이터가 처리).
        conn.execute("INSERT INTO commands(run_id, type, payload, status, created_at) VALUES (NULL, 'node_check', ?, 'queued', ?)",
                     (json.dumps({"node_id": req.id}), datetime.now().isoformat(timespec="seconds")))
    return {"ok": True}


@router.post("/nodes/{node_id}/check")
def check_node(node_id: str):
    with db.connect() as conn:
        conn.execute("INSERT INTO commands(run_id, type, payload, status, created_at) VALUES (NULL, 'node_check', ?, 'queued', ?)",
                     (json.dumps({"node_id": node_id}), datetime.now().isoformat(timespec="seconds")))
    return {"ok": True}


@router.delete("/nodes/{node_id}")
def delete_node(node_id: str):
    if node_id == "server":
        raise HTTPException(400, "홈서버 노드는 지울 수 없습니다.")
    with db.connect() as conn:
        conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
    return {"ok": True}
