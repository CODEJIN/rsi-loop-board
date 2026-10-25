"""관리자 인증: 패스워드(werkzeug 해시, Astro_Manager와 같은 방식) + TOTP.

흐름
- TOTP 미등록: 패스워드 통과 → 'enroll' 단계 → QR 등록 → 코드 확인 → 복구 코드 발급 → 로그인 완료
- TOTP 등록됨: 패스워드 + 6자리 코드(또는 복구 코드 1회) → 로그인 완료
외부 도메인에 공개되므로 IP별 로그인 실패 횟수를 제한합니다(Cloudflare 경유 시 CF-Connecting-IP 사용).
"""
import io
import os
import secrets
import time

import pyotp
import qrcode
import qrcode.image.svg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from werkzeug.security import check_password_hash, generate_password_hash

from . import db

ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
TOTP_ISSUER = os.getenv("TOTP_ISSUER", "RSI Loop")
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "12"))
TRUSTED_DAYS = int(os.getenv("TRUSTED_DAYS", "30"))
MAX_FAILURES = 5
LOCK_SEC = 15 * 60
RECOVERY_CODE_COUNT = 10

router = APIRouter(prefix="/auth")
_failures: dict[str, tuple[int, float]] = {}   # ip → (연속 실패 수, 잠금 해제 시각)


class LoginRequest(BaseModel):
    password: str
    code: str | None = None
    trust_device: bool = False


class CodeRequest(BaseModel):
    code: str


def client_ip(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")


def _check_lock(ip):
    count, until = _failures.get(ip, (0, 0))
    if until > time.time():
        raise HTTPException(429, f"로그인 시도가 너무 많습니다. {int((until - time.time()) / 60) + 1}분 후 다시 시도하세요.")


def _fail(ip, message):
    count, _ = _failures.get(ip, (0, 0))
    count += 1
    _failures[ip] = (count, time.time() + LOCK_SEC if count >= MAX_FAILURES else 0)
    raise HTTPException(401, message)


def _grant(request: Request, trust_device: bool):
    hours = TRUSTED_DAYS * 24 if trust_device else SESSION_HOURS
    request.session.clear()
    request.session.update({"admin": True, "exp": time.time() + hours * 3600})


def is_admin(request: Request) -> bool:
    s = request.session
    return bool(s.get("admin")) and s.get("exp", 0) > time.time()


def require_admin(request: Request):
    if not is_admin(request):
        raise HTTPException(401, "로그인이 필요합니다.")


def _totp_secret():
    return db.get_setting("totp_secret")


def _use_recovery_code(code: str) -> bool:
    hashes = db.get_setting("recovery_code_hashes", [])
    normalized = code.replace("-", "").replace(" ", "").lower()
    for h in hashes:
        if check_password_hash(h, normalized):
            db.set_setting("recovery_code_hashes", [x for x in hashes if x != h])
            return True
    return False


@router.get("/status")
def status(request: Request):
    return {
        "authenticated": is_admin(request),
        "stage": "enroll" if request.session.get("enroll") else None,
        "totp_enabled": _totp_secret() is not None,
        "configured": bool(ADMIN_PASSWORD_HASH),
    }


@router.post("/login")
def login(payload: LoginRequest, request: Request):
    ip = client_ip(request)
    _check_lock(ip)
    if not ADMIN_PASSWORD_HASH:
        raise HTTPException(500, "서버에 ADMIN_PASSWORD_HASH가 설정되지 않았습니다.")
    if not check_password_hash(ADMIN_PASSWORD_HASH, payload.password):
        _fail(ip, "비밀번호 또는 인증 코드가 올바르지 않습니다.")
    secret = _totp_secret()
    if secret is None:
        # 첫 로그인: 패스워드만 통과한 상태로 TOTP 등록 단계로 보냅니다(등록 전에는 다른 API 접근 불가).
        request.session.clear()
        request.session.update({"enroll": True, "enroll_exp": time.time() + 600, "trust": payload.trust_device})
        return {"ok": True, "next": "enroll"}
    code = (payload.code or "").strip()
    if not code:
        return {"ok": False, "next": "code"}
    if not (pyotp.TOTP(secret).verify(code, valid_window=1) or _use_recovery_code(code)):
        _fail(ip, "비밀번호 또는 인증 코드가 올바르지 않습니다.")
    _failures.pop(ip, None)
    _grant(request, payload.trust_device)
    return {"ok": True, "next": None}


def _require_enroll(request: Request):
    s = request.session
    if not s.get("enroll") or s.get("enroll_exp", 0) < time.time() or _totp_secret() is not None:
        raise HTTPException(401, "TOTP 등록 단계가 아닙니다. 다시 로그인하세요.")


@router.get("/totp/setup")
def totp_setup(request: Request):
    _require_enroll(request)
    secret = request.session.get("pending_secret") or pyotp.random_base32()
    request.session["pending_secret"] = secret
    uri = pyotp.TOTP(secret).provisioning_uri(name="admin", issuer_name=TOTP_ISSUER)
    buf = io.BytesIO()
    qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2).save(buf)
    return {"secret": secret, "uri": uri, "qr_svg": buf.getvalue().decode()}


@router.post("/totp/verify")
def totp_verify(payload: CodeRequest, request: Request):
    _require_enroll(request)
    secret = request.session.get("pending_secret")
    if not secret or not pyotp.TOTP(secret).verify(payload.code.strip(), valid_window=1):
        raise HTTPException(400, "코드가 맞지 않습니다. 앱에 표시된 현재 코드를 입력하세요.")
    codes = [secrets.token_hex(5) for _ in range(RECOVERY_CODE_COUNT)]
    db.set_setting("totp_secret", secret)
    db.set_setting("recovery_code_hashes", [generate_password_hash(c) for c in codes])
    trust = bool(request.session.get("trust"))
    _grant(request, trust)
    # 복구 코드는 이번 한 번만 평문으로 보여줍니다(서버에는 해시만 저장).
    return {"ok": True, "recovery_codes": [f"{c[:5]}-{c[5:]}" for c in codes]}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}
