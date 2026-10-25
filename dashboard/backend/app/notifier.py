"""텔레그램 알림.

- 봇 토큰은 .env(TELEGRAM_BOT_TOKEN)에 두고, chat_id와 알림 종류 on/off는 대시보드 설정 화면에서 DB에 저장합니다.
- 서버가 텔레그램으로 보내기만 하므로(Bot API sendMessage) 포트를 열 필요가 없습니다.
- 메시지에는 대시보드 딥링크(PUBLIC_URL/#/runs/<id>/iter/<n>)를 붙입니다.
"""
import html
import json
import logging
import os
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from . import db

logger = logging.getLogger("rsi.notifier")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
# 서버 재시작·DB 재구축 때 지난 이벤트로 알림이 쏟아지지 않도록, 이보다 오래된 이벤트는 알리지 않습니다.
MAX_EVENT_AGE = timedelta(minutes=10)
USAGE_LEVELS = (0.7, 0.9)

EVENT_TYPES = {
    "accepted": "반복 채택",
    "rejected": "반복 기각 (실패·개선 부족)",
    "run_start": "런 시작",
    "run_end": "런 완료",
    "run_aborted": "런 중단",
    "paused": "일시정지 (사용량 한도·사용자 요청)",
    "approval": "사람 승인 요청",
    "stop_recommended": "조기 종료 권고 (정체)",
    "usage": "사용량 70% / 90% 도달",
    "orchestrator": "오케스트레이터 연결 끊김",
}
DEFAULT_EVENTS = {k: k != "rejected" for k in EVENT_TYPES}
_usage_notified: dict[str, float] = {}   # 창(five_hour 등) → 이미 알린 최고 단계


def config():
    return {
        "token_configured": bool(BOT_TOKEN),
        "chat_id": db.get_setting("telegram_chat_id"),
        "events": {**DEFAULT_EVENTS, **(db.get_setting("telegram_events") or {})},
        "public_url": PUBLIC_URL,
    }


def _api(method, params=None, timeout=10):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = urllib.parse.urlencode(params or {}).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout) as r:
        return json.loads(r.read())


def send(text, chat_id=None):
    """동기 전송 — 실패하면 예외. 설정 화면의 '테스트 전송'에서 결과를 바로 보여주기 위해 씁니다."""
    chat_id = chat_id or db.get_setting("telegram_chat_id")
    if not BOT_TOKEN or not chat_id:
        raise RuntimeError("봇 토큰 또는 chat_id가 설정되지 않았습니다.")
    return _api("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"})


def send_async(text):
    def run():
        try:
            send(text)
        except Exception:
            logger.exception("telegram send failed")
    threading.Thread(target=run, daemon=True).start()


def discover_chats():
    """봇에게 최근 메시지를 보낸 대화 목록 — 사용자가 chat_id를 직접 찾지 않아도 되게 합니다."""
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN이 설정되지 않았습니다.")
    chats = {}
    for upd in _api("getUpdates").get("result", []):
        chat = (upd.get("message") or upd.get("my_chat_member") or {}).get("chat")
        if chat:
            name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")])) or chat.get("username")
            chats[chat["id"]] = {"id": chat["id"], "name": name, "type": chat.get("type")}
    return list(chats.values())


def _link(run_id, iteration=None):
    if not PUBLIC_URL:
        return ""
    path = f"#/runs/{urllib.parse.quote(run_id)}" + (f"/iter/{iteration}" if iteration else "")
    return f'\n<a href="{PUBLIC_URL}/{path}">대시보드에서 보기</a>'


def _fmt_ms(v):
    if v is None:
        return "—"
    return f"{v / 1000:.2f}s" if v >= 1000 else f"{round(v)}ms"


def _fmt(v, run):
    """런의 지표 단위에 맞춰 값 표시(ms면 초 단위 변환)."""
    unit = (json.loads(run["metric"]).get("unit") if run and run["metric"] else "ms") or ""
    if v is None:
        return "—"
    return _fmt_ms(v) if unit == "ms" else f"{round(v, 3)}{unit}"


def _run_info(run_id):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        accepted = conn.execute("SELECT COUNT(*) FROM events WHERE run_id=? AND type='judgment' AND data LIKE '%\"accepted\"%'",
                                (run_id,)).fetchone()[0]
    return row, accepted


def _iteration_summary(run_id, iteration):
    with db.connect() as conn:
        row = conn.execute("SELECT data FROM events WHERE run_id=? AND iteration=? AND type='agent_done' ORDER BY seq DESC LIMIT 1",
                           (run_id, iteration)).fetchone()
    return json.loads(row["data"]).get("summary") if row else None


def _build_message(ev, enabled):
    t, run_id = ev["type"], ev["run"]
    run, accepted = _run_info(run_id)
    spec = html.escape((run["spec"] if run else None) or run_id)
    esc = lambda s: html.escape(str(s or ""))
    if t == "judgment":
        accepted_now = ev.get("decision") == "accepted"
        if not enabled["accepted" if accepted_now else "rejected"]:
            return None
        summary = _iteration_summary(run_id, ev.get("iteration"))
        impr = ev.get("improvement", ev.get("improvement_ms"))
        head = "✅ <b>채택</b>" if accepted_now else f"▫️ <b>기각</b> ({esc(ev.get('decision'))})"
        body = f"{head} · {spec} #{ev.get('iteration')}"
        if impr is not None:
            body += f"\n개선 {_fmt(impr, run)}" if impr > 0 else f"\n악화 {_fmt(-impr, run)}"
        if accepted_now and run:
            body += f" → 현재 최고 {_fmt(run['best_objective'], run)}"
        if summary:
            body += f"\n<i>{esc(summary[:300])}</i>"
        return body + _link(run_id, ev.get("iteration"))
    if t == "approval_required" and enabled["approval"]:
        return (f"🙋 <b>승인 필요</b> · {spec} #{ev.get('iteration')}\n개선 {ev.get('improvement')} · 변경 {ev.get('diff_lines')}줄\n"
                f"사유: {esc(ev.get('reason'))}" + _link(run_id, ev.get("iteration")))
    if t == "stop_recommended" and enabled["stop_recommended"]:
        return f"🛑 <b>조기 종료 권고</b> · {spec}\n{esc(ev.get('reason'))}" + _link(run_id)
    if t == "run_start" and enabled["run_start"]:
        mode = " · 측정만(dry-run)" if ev.get("dry_run") else ""
        return f"▶️ <b>런 시작</b> · {spec}\n반복 {ev.get('iterations')}회{mode}" + _link(run_id)
    if t == "run_end" and enabled["run_end"]:
        base, best = (run["baseline_objective"], ev.get("best_objective")) if run else (None, ev.get("best_objective"))
        pct = f" ({round((best / base - 1) * 100):+d}%)" if base and best else ""
        decisions = ev.get("decisions") or []
        return (f"🏁 <b>런 완료</b> · {spec}\n{_fmt(base, run)} → <b>{_fmt(best, run)}</b>{pct}\n"
                f"채택 {decisions.count('accepted')}/{len(decisions)}" + _link(run_id))
    if t == "run_aborted" and enabled["run_aborted"]:
        return f"⛔ <b>런 중단</b> · {spec}\n{esc(ev.get('reason'))}" + _link(run_id)
    if t == "paused" and enabled["paused"]:
        return f"⏸ <b>일시정지</b> · {spec}\n{esc(ev.get('reason'))}" + _link(run_id)
    if t == "usage" and enabled["usage"]:
        msgs = []
        for window, value in (ev.get("windows") or {}).items():
            if value is None:
                continue
            level = max([lv for lv in USAGE_LEVELS if value >= lv], default=0)
            if level > _usage_notified.get(window, 0):
                msgs.append(f"{ {'five_hour': '5시간', 'seven_day': '7일'}.get(window, window)} 사용량 {round(value * 100)}%")
            # 창이 리셋되어 값이 내려가면 단계도 내려가므로, 다음에 다시 도달하면 또 알립니다.
            _usage_notified[window] = level
        if msgs:
            return "⚠️ <b>구독 사용량</b>\n" + "\n".join(msgs) + _link(run_id)
    return None


def on_event(ev):
    if not BOT_TOKEN or not db.get_setting("telegram_chat_id"):
        return
    try:
        if datetime.now() - datetime.fromisoformat(ev["ts"]) > MAX_EVENT_AGE:
            return
        text = _build_message(ev, config()["events"])
        if text:
            send_async(text)
    except Exception:
        logger.exception("notifier failed for %s", ev.get("type"))


def on_orchestrator_lost(minutes):
    if BOT_TOKEN and db.get_setting("telegram_chat_id") and config()["events"]["orchestrator"]:
        send_async(f"🔌 <b>오케스트레이터 연결 끊김</b>\n{minutes}분째 응답이 없습니다. 호스트의 rsi-orchestrator 서비스를 확인하세요."
                   + (f'\n<a href="{PUBLIC_URL}/">대시보드</a>' if PUBLIC_URL else ""))
