"""루프가 남기는 runs/<run_id>/events.jsonl 을 주기적으로 읽어 DB에 넣고, 새 이벤트를 구독자(SSE)에게 전달합니다.

루프(phase0/loop.py)와 대시보드가 서로 직접 의존하지 않도록 파일을 계약(contract)으로 둡니다.
파일 위치는 RUNS_DIRS(콜론 구분)로 여러 개 지정할 수 있습니다.
"""
import asyncio
import json
import logging
import os
from pathlib import Path

from . import db, designs, notifier

logger = logging.getLogger("rsi.ingest")
RUNS_DIRS = [Path(p) for p in os.getenv("RUNS_DIRS", "/data/runs").split(":") if p]
SCAN_INTERVAL_SEC = float(os.getenv("INGEST_INTERVAL_SEC", "1.5"))

_offsets: dict[Path, int] = {}
_subscribers: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    q = asyncio.Queue(maxsize=1000)
    _subscribers.add(q)
    return q


def unsubscribe(q):
    _subscribers.discard(q)


def _broadcast(event):
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # 너무 느린 구독자는 끊어서 메모리가 쌓이지 않게 합니다(브라우저가 자동 재연결).
            _subscribers.discard(q)


def objective_of(d):
    """지표 값 — 일반 Spec 형식은 objective, Phase 0 초기 기록은 objective_total_wait_ms."""
    return d.get("objective", d.get("objective_total_wait_ms"))


def _apply_to_run(conn, ev, source_path):
    t, run_id, data = ev["type"], ev["run"], ev
    conn.execute("INSERT OR IGNORE INTO runs(id, source_path, status) VALUES (?, ?, 'running')", (run_id, str(source_path)))
    conn.execute("UPDATE runs SET last_event_at = ? WHERE id = ?", (ev["ts"], run_id))
    if t == "run_start":
        conn.execute(
            "UPDATE runs SET spec=?, spec_version=?, repo=?, started_at=?, status='running', baseline_objective=?, "
            "best_objective=?, noise_threshold_ms=?, planned_iterations=?, title=?, metric=?, judge=?, dry_run=? WHERE id=?",
            (data.get("spec"), data.get("version"), data.get("repo"), ev["ts"], data.get("baseline_objective"),
             data.get("baseline_objective"), data.get("noise_threshold", data.get("noise_threshold_ms")), data.get("iterations"),
             data.get("title"), json.dumps(data["metric"], ensure_ascii=False) if data.get("metric") else None,
             data.get("judge"), 1 if data.get("dry_run") else 0, run_id))
    elif t == "baseline_measured":
        value = objective_of(data)
        conn.execute("UPDATE runs SET baseline_objective=?, best_objective=COALESCE(best_objective, ?) WHERE id=?",
                     (value, value, run_id))
    elif t == "judgment" and data.get("decision") == "accepted":
        row = conn.execute("SELECT data FROM events WHERE run_id=? AND type='metric' AND iteration=? ORDER BY seq DESC LIMIT 1",
                           (run_id, data.get("iteration"))).fetchone()
        if row:
            conn.execute("UPDATE runs SET best_objective=? WHERE id=?", (objective_of(json.loads(row["data"])), run_id))
    elif t == "usage":
        w, r = data.get("windows") or {}, data.get("resets") or {}
        conn.execute("INSERT INTO usage_samples(ts, five_hour, seven_day, five_reset, seven_reset, source, run_id) "
                     "VALUES (?, ?, ?, ?, ?, 'loop', ?)", (ev["ts"], w.get("five_hour"), w.get("seven_day"),
                                                          r.get("five_hour"), r.get("seven_day"), run_id))
    elif t == "paused":
        conn.execute("UPDATE runs SET status='paused' WHERE id=?", (run_id,))
    elif t == "resumed":
        conn.execute("UPDATE runs SET status='running' WHERE id=?", (run_id,))
    elif t == "run_aborted":
        conn.execute("UPDATE runs SET status='aborted', ended_at=? WHERE id=?", (ev["ts"], run_id))
    elif t == "run_end":
        conn.execute("UPDATE runs SET status='finished', ended_at=?, best_objective=COALESCE(?, best_objective) WHERE id=?",
                     (ev["ts"], data.get("best_objective"), run_id))


def _mark_commands_applied(conn, ev):
    """루프가 명령을 반영하면 이벤트에 command_id(들)를 남깁니다 → 해당 명령을 '반영됨'으로 바꿉니다.
    런이 끝났는데 아직 반영되지 못한 명령(예: 다음 반복용 추가 지시)은 '만료됨'으로 정리합니다."""
    ids = ev.get("command_ids") or ([ev["command_id"]] if ev.get("command_id") else [])
    for cid in ids:
        conn.execute("UPDATE commands SET status='applied', applied_at=?, run_id=COALESCE(run_id, ?) "
                     "WHERE id=? AND status IN ('queued','delivered')", (ev["ts"], ev["run"], cid))
    if ev["type"] in ("run_end", "run_aborted"):
        conn.execute("UPDATE commands SET status='expired', result='런이 먼저 끝나 반영되지 않음' "
                     "WHERE run_id=? AND status IN ('queued','delivered')", (ev["run"],))


def _ingest_file(path: Path):
    size = path.stat().st_size
    start = _offsets.get(path, 0)
    if size < start:      # 파일이 새로 쓰였으면 처음부터
        start = 0
    if size == start:
        return []
    new_events = []
    with open(path, "rb") as f:
        f.seek(start)
        chunk = f.read()
    # 마지막 줄이 아직 쓰는 중일 수 있으므로 완성된 줄까지만 처리합니다.
    end = chunk.rfind(b"\n") + 1
    if end == 0:
        return []
    with db.connect() as conn:
        for line in chunk[:end].splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO events(run_id, seq, ts, type, iteration, data) VALUES (?, ?, ?, ?, ?, ?)",
                (ev["run"], ev["seq"], ev["ts"], ev["type"], ev.get("iteration"), json.dumps(ev, ensure_ascii=False)))
            if cur.rowcount:
                _apply_to_run(conn, ev, path)
                _mark_commands_applied(conn, ev)
                new_events.append(ev)
    _offsets[path] = start + end
    return new_events


def rebuild_runs():
    """저장된 이벤트를 처음부터 다시 적용해 runs 요약을 재계산합니다(서버 시작 시 1회).
    요약 규칙이 바뀌어도 이미 들어온 이벤트에 새 규칙이 반영되게 하기 위함입니다."""
    with db.connect() as conn:
        rows = conn.execute("SELECT r.source_path, e.data FROM events e JOIN runs r ON r.id = e.run_id "
                            "ORDER BY e.run_id, e.seq").fetchall()
        conn.execute("DELETE FROM runs")
        conn.execute("DELETE FROM usage_samples WHERE source='loop'")
        for row in rows:
            _apply_to_run(conn, json.loads(row["data"]), row["source_path"])


def scan_once():
    new = []
    for d in RUNS_DIRS:
        if not d.exists():
            continue
        for path in sorted(d.glob("*/events.jsonl")):
            try:
                new.extend(_ingest_file(path))
            except Exception:
                logger.exception("ingest failed: %s", path)
    return new


async def ingest_loop():
    while True:
        try:
            for ev in await asyncio.to_thread(scan_once):
                _broadcast(ev)
                notifier.on_event(ev)
            await asyncio.to_thread(designs.process_spec_saves)
        except Exception:
            logger.exception("ingest loop error")
        await asyncio.sleep(SCAN_INTERVAL_SEC)
