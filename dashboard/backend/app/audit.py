"""감사 로그 — 상태를 바꾸는 모든 요청(POST/DELETE)을 미들웨어 한 곳에서 기록합니다.

핸들러마다 기록 코드를 넣으면 빠뜨리기 쉬워서, ASGI 미들웨어가 요청 본문과 응답 상태를 함께 남깁니다.
비밀번호·OTP·스크립트 본문 같은 값은 저장하지 않습니다.
"""
import json
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from . import auth, db

REDACT = {"password", "code", "content", "yaml", "text", "message"}   # 값 대신 길이만 남김(비밀·대용량)
KEEP_TEXT = {"text", "message"}                                         # 짧은 앞부분만 남길 키
RETENTION_DAYS = 180

# 경로 → 사람이 읽는 동작 이름 (위에서부터 처음 맞는 것)
ACTIONS = [
    (r"^/auth/login$", "auth", "로그인"),
    (r"^/auth/logout$", "auth", "로그아웃"),
    (r"^/auth/totp/verify$", "auth", "OTP 등록"),
    (r"^/api/commands$", "command", "명령"),
    (r"^/api/specs/validate$", None, None),           # 검사만 하는 요청은 기록하지 않음
    (r"^/api/specs$", "spec", "Spec 저장"),
    (r"^/api/schedules/\d+$", "schedule", "일정 삭제"),
    (r"^/api/schedules$", "schedule", "일정 저장"),
    (r"^/api/runs/[^/]+/meta$", "run", "런 보관 변경"),
    (r"^/api/runs/[^/]+$", "run", "런 삭제"),
    (r"^/api/settings/telegram/test$", "settings", "텔레그램 테스트"),
    (r"^/api/settings/telegram/discover$", None, None),
    (r"^/api/settings/telegram$", "settings", "텔레그램 설정"),
    (r"^/api/settings/models$", "settings", "모델 계층 설정"),
    (r"^/api/nodes/[^/]+/check$", None, None),
    (r"^/api/nodes/[^/]+$", "node", "노드 삭제"),
    (r"^/api/nodes$", "node", "노드 저장"),
    (r"^/api/design$", "design", "설계 대화 시작"),
    (r"^/api/design/\d+/messages$", "design", "설계 메시지"),
    (r"^/api/design/\d+/save-script$", "design", "평가 스크립트 저장"),
    (r"^/api/design/\d+/workspace$", "design", "작업 공간 생성"),
    (r"^/api/design/\d+/save-spec$", "spec", "설계 보드에서 Spec 저장"),
    (r"^/api/design/\d+/init-repo$", "design", "원본 git 초기화"),
    (r"^/api/design/\d+/actions/[\d:]+$", "design", "설계 실행 요청 승인"),
]


def _classify(method, path):
    for pattern, cat, label in ACTIONS:
        if re.match(pattern, path):
            return cat, label
    return "other", f"{method} {path}"


def _sanitize(body):
    if not isinstance(body, dict):
        return None
    out = {}
    for k, v in body.items():
        if k in REDACT:
            if k in KEEP_TEXT and isinstance(v, str):
                out[k] = v[:80] + ("…" if len(v) > 80 else "")
            else:
                out[k] = f"<{len(str(v))}자>" if k not in ("password", "code") else "<숨김>"
        elif isinstance(v, dict):
            out[k] = _sanitize(v)
        else:
            out[k] = v
    return out


class AuditMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "DELETE", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        chunks, status = [], {"code": None}

        async def recv():
            msg = await receive()
            if msg["type"] == "http.request":
                chunks.append(msg.get("body", b""))
            return msg

        async def snd(msg):
            if msg["type"] == "http.response.start":
                status["code"] = msg["status"]
            await send(msg)

        try:
            await self.app(scope, recv, snd)
        finally:
            try:
                self._record(scope, b"".join(chunks), status["code"])
            except Exception:
                pass   # 감사 기록 실패가 요청 자체를 망치지 않게

    def _record(self, scope, raw, code):
        cat, label = _classify(scope["method"], scope["path"])
        if cat is None:
            return
        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        ip = headers.get("cf-connecting-ip") or (scope.get("client") or ["?"])[0]
        try:
            body = json.loads(raw) if raw else None
        except (ValueError, UnicodeDecodeError):
            body = None
        detail = _sanitize(body) or {}
        if cat == "command" and isinstance(body, dict):
            label = f"명령: {body.get('type')}"
        ua = headers.get("user-agent", "")
        device = "모바일" if re.search(r"Mobile|Android|iPhone", ua) else "PC" if ua else "?"
        with db.connect() as conn:
            conn.execute("INSERT INTO audit_log(ts, category, action, path, method, status, ip, device, detail) VALUES (?,?,?,?,?,?,?,?,?)",
                         (datetime.now().isoformat(timespec="seconds"), cat, label, scope["path"], scope["method"], code, ip, device,
                          json.dumps(detail, ensure_ascii=False)))


def prune():
    with db.connect() as conn:
        conn.execute("DELETE FROM audit_log WHERE ts < ?", ((datetime.now() - timedelta(days=RETENTION_DAYS)).isoformat(),))


router = APIRouter(prefix="/api", dependencies=[Depends(auth.require_admin)])


@router.get("/audit")
def audit_list(category: str | None = None, failed: bool = False, limit: int = 200):
    q, args = "SELECT * FROM audit_log WHERE 1=1", []
    if category:
        q += " AND category=?"; args.append(category)
    if failed:
        q += " AND status >= 400"
    q += " ORDER BY id DESC LIMIT ?"; args.append(min(limit, 1000))
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(q, args)]
    for r in rows:
        r["detail"] = json.loads(r["detail"] or "{}")
    return rows
