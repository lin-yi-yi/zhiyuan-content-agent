"""Opaque sessions, CSRF and bounded single-process authentication defenses."""
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import math
import os
import re
import secrets
from threading import BoundedSemaphore, Lock
import time

from fastapi import HTTPException, Request, Response

from app.saas.context import TenantContext, is_saas_mode
from app.saas.models import SaaSAuditLog, SaaSMembership, SaaSOrganization, SaaSSession, SaaSUser
from app.saas.store import session_factory


SESSION_COOKIE = "zhiyuan_session"
SESSION_SECONDS = 7 * 24 * 60 * 60
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 17, 8, 1
_PASSWORD_SLOTS = BoundedSemaphore(2)
AUTH_WINDOW_SECONDS = 15 * 60
AUTH_IP_LIMIT, AUTH_ACCOUNT_LIMIT = 40, 10
MAX_RATE_BUCKETS = 2048
_rate_lock = Lock()
_rate_buckets = OrderedDict()


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.astimezone(timezone.utc).isoformat()


def env_bool(name, default=False):
    return os.getenv(name, "true" if default else "false").strip().lower() in {"1", "true", "yes", "on"}


def allow_registration():
    return env_bool("SAAS_ALLOW_REGISTRATION", True)


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token(token):
    return hmac.new(token.encode("utf-8"), b"zhiyuan-session-csrf-v1", hashlib.sha256).hexdigest()


def _derive(password, salt, n, r, p):
    if not _PASSWORD_SLOTS.acquire(timeout=2):
        raise HTTPException(429, "登录请求较多，请稍后重试", headers={"Retry-After": "2"})
    try:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                              dklen=32, maxmem=256 * 1024 * 1024)
    finally:
        _PASSWORD_SLOTS.release()


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password, encoded):
    try:
        algorithm, n, r, p, salt_hex, expected_hex = encoded.split("$")
        n, r, p = int(n), int(r), int(p)
        # Stored parameters are bounded too: a damaged record must not allocate
        # arbitrary memory or silently switch to another password algorithm.
        if algorithm != "scrypt" or n < 2 ** 12 or n > 2 ** 17 or n & (n - 1) or r != 8 or p != 1:
            return False
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(expected_hex)
        if len(salt) != 16 or len(expected) != 32:
            return False
        return hmac.compare_digest(_derive(password, salt, n, r, p), expected)
    except (ValueError, TypeError, AttributeError):
        return False


def dummy_password_check(password):
    # Unknown accounts pay the same configured KDF cost; no real user's hash is reused.
    _derive(password, b"anonymous-login!", SCRYPT_N, SCRYPT_R, SCRYPT_P)


def reset_rate_limits():
    with _rate_lock:
        _rate_buckets.clear()


def check_auth_rate_limit(request, email):
    stamp = time.monotonic()
    ip = request.client.host if request.client else "unknown"
    # Proxy-supplied headers are deliberately not trusted as client identity.
    keys = [("ip", ip, AUTH_IP_LIMIT), ("account", token_hash(email.casefold()), AUTH_ACCOUNT_LIMIT)]
    with _rate_lock:
        try:
            for kind, value, limit in keys:
                key = (kind, value)
                bucket = _rate_buckets.setdefault(key, deque())
                while bucket and bucket[0] <= stamp - AUTH_WINDOW_SECONDS:
                    bucket.popleft()
                _rate_buckets.move_to_end(key)
                if len(bucket) >= limit:
                    delay = max(1, math.ceil(bucket[0] + AUTH_WINDOW_SECONDS - stamp))
                    raise HTTPException(429, "尝试次数过多，请稍后重试", headers={"Retry-After": str(delay)})
            for kind, value, _ in keys:
                _rate_buckets[(kind, value)].append(stamp)
        finally:
            # Rejected requests can introduce new IPs too; every exit is bounded.
            while len(_rate_buckets) > MAX_RATE_BUCKETS:
                _rate_buckets.popitem(last=False)


def read_session(db, request, *, required=True):
    raw = request.cookies.get(SESSION_COOKIE, "")
    session = None
    if re.fullmatch(r"[A-Za-z0-9_-]{32,128}", raw):
        session = db.query(SaaSSession).filter(SaaSSession.token_hash == token_hash(raw),
            SaaSSession.revoked_at.is_(None), SaaSSession.expires_at > now()).first()
    user = db.get(SaaSUser, session.user_id) if session else None
    if not session or not user or not user.is_active:
        if required:
            raise HTTPException(401, "请先登录或重新登录")
        return None, None, ""
    return session, user, raw


def user_organizations(db, user_id):
    rows = db.query(SaaSMembership, SaaSOrganization).join(SaaSOrganization,
        SaaSOrganization.id == SaaSMembership.organization_id).filter(
            SaaSMembership.user_id == user_id, SaaSMembership.is_active.is_(True)
        ).order_by(SaaSMembership.created_at, SaaSMembership.id).all()
    return [{"id": org.id, "name": org.name, "role": member.role} for member, org in rows]


def selected_organization(request, session, organizations, *, strict=True):
    selected = request.headers.get("X-Organization-ID")
    if selected:
        result = next((org for org in organizations if org["id"] == selected), None)
        if result is None and strict:
            raise HTTPException(403, "无权访问此组织")
        if result is not None:
            return result
    return next((org for org in organizations if org["id"] == session.active_organization_id),
                organizations[0] if organizations else None)


def resolve_identity(request: Request) -> TenantContext:
    with session_factory() as db:
        session, user, _ = read_session(db, request)
        organizations = user_organizations(db, user.id)
        org = selected_organization(request, session, organizations)
        if org is None:
            raise HTTPException(403, "当前账号没有可访问的组织，请接受邀请或创建组织")
        return TenantContext(organization_id=org["id"], user_id=user.id, role=org["role"], organization_name=org["name"])


def validate_csrf(request: Request):
    if not is_saas_mode() or request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    with session_factory() as db:
        _, _, raw = read_session(db, request)
    supplied = request.headers.get("X-CSRF-Token", "")
    if not re.fullmatch(r"[0-9a-f]{64}", supplied) or not hmac.compare_digest(supplied, csrf_token(raw)):
        raise HTTPException(403, "请求校验失败，请刷新登录状态后重试")


def session_payload(request, db):
    base = {"mode": "saas" if is_saas_mode() else "local", "authenticated": False, "user": None,
            "organizations": [], "active_organization_id": None, "csrf_token": None,
            "allow_registration": allow_registration()}
    if not is_saas_mode():
        return base
    session, user, raw = read_session(db, request, required=False)
    if not session:
        return base
    organizations = user_organizations(db, user.id)
    # Bootstrap must recover after a membership was revoked. Business identity
    # resolution remains strict and never accepts an unauthorized organization.
    org = selected_organization(request, session, organizations, strict=False)
    return {**base, "authenticated": True, "user": {"id": user.id, "email": user.email, "name": user.name},
            "organizations": organizations, "active_organization_id": org["id"] if org else None,
            "csrf_token": csrf_token(raw)}


def issue_session(db, request, response: Response, user, organization_id):
    stamp = now()
    previous = request.cookies.get(SESSION_COOKIE)
    if previous:
        db.query(SaaSSession).filter(SaaSSession.token_hash == token_hash(previous)).update({"revoked_at": stamp})
    raw = secrets.token_urlsafe(32)
    session = SaaSSession(user_id=user.id, token_hash=token_hash(raw), active_organization_id=organization_id,
                         created_at=stamp, expires_at=stamp + timedelta(seconds=SESSION_SECONDS))
    db.add(session)
    response.set_cookie(SESSION_COOKIE, raw, max_age=SESSION_SECONDS, path="/", httponly=True,
                        secure=env_bool("SAAS_SECURE_COOKIE", True), samesite="lax")
    organizations = user_organizations(db, user.id)
    return {"mode": "saas", "authenticated": True, "user": {"id": user.id, "email": user.email, "name": user.name},
            "organizations": organizations, "active_organization_id": organization_id,
            "csrf_token": csrf_token(raw), "allow_registration": allow_registration()}


def _audit_details(value, depth=0):
    if depth > 4:
        return "[truncated]"
    if isinstance(value, dict):
        return {str(key)[:100]: ("[redacted]" if re.search(r"password|token|secret|api.?key|cookie|authorization|content|body|prompt", str(key), re.I)
                 else _audit_details(item, depth + 1)) for key, item in list(value.items())[:30]}
    if isinstance(value, list):
        return [_audit_details(item, depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return value[:300]
    return value if value is None or isinstance(value, (bool, int, float)) else str(value)[:100]


def audit_in_transaction(db, ctx, action, resource="", details=None):
    db.add(SaaSAuditLog(organization_id=ctx.organization_id if ctx else None,
        actor_user_id=ctx.user_id if ctx else None, action=action[:100], resource=resource[:200],
        details_json=_audit_details(details or {}), created_at=now()))


def record_audit(ctx, action: str, resource: str = "", details: dict | None = None):
    with session_factory() as db:
        audit_in_transaction(db, ctx, action, resource, details)
        db.commit()
