"""SaaS boundary: authenticate before any business DB access, including legacy APIs."""
import os
import json
import re
import uuid
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.saas.context import current_tenant, is_saas_mode

SAFE = {"GET", "HEAD", "OPTIONS"}
PUBLIC = {"/health", "/api/health", "/api/saas/session"}
ACCOUNT = {"/api/saas/organizations", "/api/saas/invites/accept", "/api/saas/auth/logout"}
LOGIN = {"/api/saas/auth/login", "/api/saas/auth/register"}
QUERY_POSTS = {"/api/v04/rag/search", "/api/v04/rag/answer", "/api/models/chat"}
MAX_BODY = 2 * 1024 * 1024


def public_origin():
    value = os.getenv("SAAS_PUBLIC_ORIGIN", "").rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("SAAS_PUBLIC_ORIGIN 必须是明确的 http(s) 站点地址")
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
        raise RuntimeError("公网 SaaS 必须使用 HTTPS")
    if parsed.scheme == "https" and os.getenv("SAAS_SECURE_COOKIE", "true").lower() not in {"true", "1", "yes", "on"}:
        raise RuntimeError("HTTPS SaaS 必须开启 SAAS_SECURE_COOKIE")
    return value


def authorize(role, method, path):
    if method in SAFE:
        return
    if path.startswith("/api/saas/"):
        # Account routes have their own checks; all tenant control-plane writes are owner-only.
        if path not in ACCOUNT and role != "owner":
            raise HTTPException(403, "只有组织所有者可以修改团队设置")
        return
    if path in QUERY_POSTS:
        return
    if re.fullmatch(r"/api/(agent-runs/[^/]+/review|evidence/notes/[^/]+/review|drafts/[^/]+/review-checklist)", path):
        allowed = {"owner", "reviewer"}
    elif re.fullmatch(r"/api/evidence/notes/[^/]+/index", path):
        allowed = {"owner", "editor", "reviewer"}
    elif path.startswith("/api/models/test/") or path == "/api/v04/tools/execute":
        allowed = {"owner"}
    else:
        allowed = {"owner", "editor"}
    if role not in allowed:
        raise HTTPException(403, "当前角色无权执行此操作")


def consumes_ai(method, path, body=b""):
    if method != "POST":
        return False
    if path in {"/api/topics/custom-ideas/confirm", "/api/topics/import-url/confirm", "/api/topics/import-url"} or re.fullmatch(r"/api/sources/[^/]+/topic-ideas/confirm", path):
        try:
            return json.loads(body).get("auto_score") is True
        except (ValueError, AttributeError):
            return False
    if path in {"/api/models/chat", "/api/v04/rag/answer", "/api/v04/tools/execute", "/api/agent-runs",
                "/api/topics/custom-ideas"}:
        return True
    return bool(re.fullmatch(r"/api/(models/test/[^/]+|agent-runs/[^/]+/retry|topics/[^/]+/(score|generate-draft)|"
        r"sources/[^/]+/topic-ideas|drafts/[^/]+/(generate-variant|evaluate))", path))


class SaaSMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not is_saas_mode():
            return await self.app(scope, receive, send)
        request = Request(scope)
        path, method = scope.get("path", "").rstrip("/") or "/", scope["method"]
        tenant_token = reservation = None
        started = False
        body = b""
        try:
            origin = public_origin()
            if request.headers.get("host", "").lower() != urlsplit(origin).netloc.lower():
                raise HTTPException(400, "站点地址不匹配")
            # UI and liveness are public; API/readiness always pass through identity checks.
            protected = path.startswith("/api/") or path == "/ready"
            if protected and method not in SAFE:
                if request.headers.get("origin") != origin:
                    raise HTTPException(403, "来源校验失败，请从本站页面操作")
                if path not in LOGIN:
                    from app.saas.auth import validate_csrf
                    await run_in_threadpool(validate_csrf, request)
                # Bound chunked bodies too, before endpoints parse or allocate content.
                body = bytearray()
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > MAX_BODY:
                        raise HTTPException(413, "请求内容过大，请分批导入")
                    if not message.get("more_body", False):
                        break
                delivered = False
                upstream_receive = receive
                async def replay():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await upstream_receive()
                receive = replay
            if protected and path not in PUBLIC | LOGIN | ACCOUNT:
                from app.saas.auth import resolve_identity
                context = await run_in_threadpool(resolve_identity, request)
                authorize(context.role, method, path)
                tenant_token = current_tenant.set(context)
                if consumes_ai(method, path, body):
                    from app.saas.commerce import reserve_usage
                    from app.core.diagnostics import current_request_id, valid_request_id
                    # Correlate new ledger events to the server-generated request,
                    # never a caller header. Standalone middleware keeps a fresh ID.
                    usage_request_id = valid_request_id(current_request_id.get()) or uuid.uuid4().hex
                    reservation = await run_in_threadpool(reserve_usage, context.organization_id, usage_request_id)
            else:
                context = None

            async def wrapped_send(message):
                nonlocal started, reservation
                if message["type"] == "http.response.start":
                    status = message["status"]
                    if reservation:
                        from app.saas.commerce import finalize_usage
                        await run_in_threadpool(finalize_usage, reservation, status < 400)
                        reservation = None
                    if context and method not in SAFE and status < 400:
                        from app.saas.auth import record_audit
                        await run_in_threadpool(record_audit, context, "api.mutation", path, {"method": method, "status": status})
                    headers = list(message.get("headers", []))
                    if protected:
                        headers = [(k,v) for k,v in headers if k.lower() != b"cache-control"]
                        headers.append((b"cache-control", b"no-store"))
                    headers.extend([(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"same-origin"),
                                    (b"x-frame-options", b"DENY")])
                    message["headers"] = headers
                    started = True
                await send(message)
            await self.app(scope, receive, wrapped_send)
        except HTTPException as exc:
            if started:
                raise
            await JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)(scope, receive, send)
        except Exception as exc:
            from app.saas.commerce import CommerceError, QuotaExceeded, UsageConflict
            if started:
                raise
            if isinstance(exc, CommerceError):
                status = 429 if isinstance(exc, QuotaExceeded) else 409 if isinstance(exc, UsageConflict) else 422
                await JSONResponse({"detail": str(exc)}, status_code=status)(scope, receive, send)
            else:
                from app.core.diagnostics import emit_diagnostic, safe_error_type
                emit_diagnostic("saas_boundary_failed", error_type=safe_error_type(exc))
                await JSONResponse({"detail": "服务暂时不可用，请稍后重试"}, status_code=503)(scope, receive, send)
        finally:
            if reservation:
                from app.saas.commerce import finalize_usage
                await run_in_threadpool(finalize_usage, reservation, False)
            if tenant_token is not None:
                current_tenant.reset(tenant_token)
