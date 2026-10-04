"""Small, allowlisted diagnostic events; never request/model/document payloads.

Correlation is metadata, not authorization. HTTP IDs are always generated here;
background workers receive them explicitly and restore context on exit.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import math
import re
import time
from uuid import uuid4

from starlette.responses import JSONResponse


LOGGER = logging.getLogger("content_agent.diagnostics")
current_request_id: ContextVar[str | None] = ContextVar("diagnostic_request_id", default=None)
_EVENTS = {
    "http_response", "http_failed", "http_failed_after_response", "saas_boundary_failed",
    "workflow_started", "workflow_finished", "workflow_step_failed", "workflow_background_failed",
}
_CODES = {"REQUEST_FAILED", "BACKGROUND_FAILED", "INSUFFICIENT_EVIDENCE", "INVALID_CITATIONS",
          "WORKFLOW_CONFLICT", "RETRIEVAL_FAILED", "MODEL_CALL_FAILED", "STEP_FAILED"}
_ERROR_TYPES = {
    "ValueError", "TypeError", "RuntimeError", "OSError", "TimeoutError", "ConnectionError",
    "PermissionError", "FileNotFoundError", "OperationalError", "IntegrityError", "StatementError",
    "SQLAlchemyError", "ConnectionUnavailable", "ModelCallError", "RetrievalError", "EvidenceError",
    "CitationError", "WorkflowConflict", "WorkflowCancelled", "UnexpectedError",
}
_STEPS = {"retrieve_context", "topic_ideas", "create_topic", "score_topic", "generate_draft",
          "generate_cards", "compliance_check", "evaluate_package", "revise_package",
          "reevaluate_package", "agent_decision", "human_review", "queued", "cancelled"}
_STATUSES = {"pending", "running", "failed", "cancelled", "awaiting_review", "approved", "rejected"}


class _SafeStreamHandler(logging.StreamHandler):
    def handleError(self, record):
        # A broken diagnostics sink must not dump a traceback or fail the task.
        pass


def configure_diagnostics_logging():
    if not LOGGER.handlers:
        handler = _SafeStreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    # Uvicorn's access formatter includes raw paths/query strings. The fixed
    # http_response event replaces it; lifecycle/error logging remains enabled.
    logging.getLogger("uvicorn.access").disabled = True


def valid_request_id(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value) else None


@contextmanager
def request_diagnostic_context(request_id):
    token = current_request_id.set(valid_request_id(request_id))
    try:
        yield current_request_id.get()
    finally:
        current_request_id.reset(token)


def safe_error_type(exc):
    name = type(exc).__name__
    return name if name in _ERROR_TYPES else "UnexpectedError"


def emit_diagnostic(event, *, agent_run_id=None, agent_step_id=None, step_key=None,
                    workflow_attempt=None, status=None, code=None, error_type=None,
                    elapsed_ms=None, http_status=None, method=None, route=None):
    """Only explicit fields/enumerations; no arbitrary extra dicts or exceptions."""
    if event not in _EVENTS:
        return
    from app.saas.context import current_tenant
    tenant = current_tenant.get()
    positive_id = lambda value: value if type(value) is int and value > 0 else None
    duration = (round(elapsed_ms, 1) if type(elapsed_ms) in {int, float}
                and math.isfinite(elapsed_ms) and elapsed_ms >= 0 else None)
    payload = {
        "event": event, "at": datetime.now(timezone.utc).isoformat(),
        "request_id": current_request_id.get(),
        "organization_id": valid_request_id(tenant.organization_id) if tenant else None,
        "agent_run_id": positive_id(agent_run_id), "agent_step_id": positive_id(agent_step_id),
        "workflow_attempt": positive_id(workflow_attempt),
        "step_key": step_key if step_key in _STEPS else None,
        "status": status if status in _STATUSES else None, "code": code if code in _CODES else None,
        "error_type": error_type if error_type in _ERROR_TYPES else None,
        "elapsed_ms": duration,
        "http_status": http_status if type(http_status) is int and 100 <= http_status <= 599 else None,
        "method": method if method in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"} else None,
        # This argument comes only from a matched, registered route, never URL.path.
        "route": route,
    }
    try:
        LOGGER.log(logging.WARNING if error_type or (http_status or 0) >= 400 else logging.INFO,
                   json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        # Best effort only: emitting metadata must never change business results.
        pass


def _route_template(scope):
    return getattr(scope.get("route"), "path", None) or "<unmatched>"


async def _safe_request(app, scope, receive, send):
    started = False
    start = time.perf_counter()

    async def guarded_send(message):
        nonlocal started
        if message["type"] == "http.response.start":
            started = True
        await send(message)

    try:
        await app(scope, receive, guarded_send)
    except Exception as exc:
        emit_diagnostic("http_failed_after_response" if started else "http_failed",
                        code="REQUEST_FAILED", error_type=safe_error_type(exc),
                        method=scope.get("method"), route=_route_template(scope),
                        elapsed_ms=(time.perf_counter() - start) * 1000)
        if not started:
            await JSONResponse(status_code=500, content={
                "detail": "处理失败，请查看运行日志并重试。", "request_id": current_request_id.get(),
            })(scope, receive, send)
        # A sent response cannot be replaced. Do not rethrow provider/SQL text
        # into the server's traceback after logging the bounded failure event.


class RequestErrorsMiddleware:
    """Preserve business HTTP 500 responses inside the separate SaaS boundary."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        await _safe_request(self.app, scope, receive, send)


class RequestDiagnosticsMiddleware:
    """Pure ASGI wrapper, outside SaaS rejection and through background completion."""
    def __init__(self, app):
        self.app = app
        # Also covers uvicorn.run(app), where logging may be configured after
        # importing main. Middleware construction happens before serving HTTP.
        configure_diagnostics_logging()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        start = time.perf_counter()

        async def traced_send(message):
            if message["type"] == "http.response.start":
                elapsed = (time.perf_counter() - start) * 1000
                headers = [(key, value) for key, value in message.get("headers", [])
                           if key.lower() not in {b"x-request-id", b"server-timing", b"x-content-type-options"}]
                headers.extend([(b"x-request-id", request_id.encode()),
                                (b"server-timing", f"app;dur={elapsed:.1f}".encode()),
                                (b"x-content-type-options", b"nosniff")])
                message = {**message, "headers": headers}
                emit_diagnostic("http_response", http_status=message["status"], method=scope.get("method"),
                                route=_route_template(scope), elapsed_ms=elapsed)
            await send(message)

        with request_diagnostic_context(request_id):
            await _safe_request(self.app, scope, receive, traced_send)
