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
import os
import sys
from threading import RLock
import re
import time
from uuid import uuid4

from starlette.responses import JSONResponse

from app.core.diagnostic_schema import (EVENTS as _EVENTS, CODES as _CODES,
    ERROR_TYPES as _ERROR_TYPES, STEPS as _STEPS, STATUSES as _STATUSES,
    valid_request_id, safe_route_template)

LOGGER = logging.getLogger("content_agent.diagnostics")
current_request_id: ContextVar[str | None] = ContextVar("diagnostic_request_id", default=None)

_store_guard = RLock()
_active_store = None
_active_lifetimes = set()
_sink_notices = set()


class DiagnosticConfigurationError(RuntimeError):
    """Fixed startup error without private paths or configuration values."""


def _sink_failure_notice(code="DIAGNOSTIC_SINK_DEGRADED"):
    events = {"DIAGNOSTIC_SINK_DEGRADED": "diagnostic_sink_degraded",
              "DIAGNOSTIC_RECORD_REJECTED": "diagnostic_record_rejected"}
    if code not in events or code in _sink_notices:
        return
    _sink_notices.add(code)
    try:
        sys.stderr.write(json.dumps({"event": events[code], "code": code}, separators=(",", ":")) + "\n")
        sys.stderr.flush()
    except Exception:
        pass  # This fallback can fail too; never claim delivery or change business results.


@contextmanager
def persistent_diagnostics():
    """Only the application lifespan owns the optional file sink, never imports.

    A process-global sink cannot distinguish simultaneous app lifetimes; reject
    overlap involving a configured sink instead of copying another app's events.
    Default disabled lifetimes may overlap and never open a filesystem path.
    """
    global _active_store, _sink_notices
    directory = os.environ.get("DIAGNOSTIC_LOG_DIR", "")
    owner = object()
    store = None
    with _store_guard:
        if _active_store is not None or (directory and _active_lifetimes):
            raise DiagnosticConfigurationError("诊断日志已有应用实例占用，请先停止已有实例。")
        if directory:
            from app.core.diagnostic_store import DiagnosticStore
            try:
                values = [os.environ.get("DIAGNOSTIC_LOG_MAX_BYTES", "1048576"),
                          os.environ.get("DIAGNOSTIC_LOG_BACKUP_COUNT", "5")]
                if not all(re.fullmatch(r"[0-9]{1,9}", value) for value in values):
                    raise ValueError()
                store = DiagnosticStore(directory, max_bytes=int(values[0]), backup_count=int(values[1]))
            except Exception:
                raise DiagnosticConfigurationError("诊断日志配置无效或目录不可用，请检查私有目录、权限与单进程占用。") from None
            _active_store = store
            _sink_notices = set()
        _active_lifetimes.add(owner)
    try:
        yield store
    finally:
        with _store_guard:
            if store is not None and _active_store is store:
                try:
                    store.close()
                    if store.status()["error_code"] == "CLOSE_FAILED":
                        _sink_failure_notice()
                except Exception:
                    _sink_failure_notice()
                finally:
                    _active_store = None
            _active_lifetimes.discard(owner)


def diagnostic_sink_status():
    """Process-local metadata for operators/tests, not a public cross-tenant API."""
    with _store_guard:
        if _active_store is None:
            return {"state": "disabled", "written": 0, "dropped": 0, "error_code": None}
        return _active_store.status()


def _persist_diagnostic(payload):
    with _store_guard:
        if _active_store is not None:
            try:
                if not _active_store.append(payload):
                    code = ("DIAGNOSTIC_RECORD_REJECTED" if _active_store.status()["error_code"] == "INVALID_RECORD"
                            else "DIAGNOSTIC_SINK_DEGRADED")
                    _sink_failure_notice(code)
            except Exception:
                _sink_failure_notice()


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
                    elapsed_ms=None, http_status=None, method=None, route=None, usage_outcome=None):
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
        "usage_outcome": usage_outcome if usage_outcome in {"accepted", "rejected", "unknown"} else None,
        "method": method if method in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"} else None,
        # This argument comes only from a matched, registered route, never URL.path.
        "route": safe_route_template(route),
    }
    try:
        LOGGER.log(logging.WARNING if error_type or (http_status or 0) >= 400
                   or event in {"usage_finalization_failed", "usage_outcome_unknown"} else logging.INFO,
                   json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        # Best effort only: emitting metadata must never change business results.
        pass
    _persist_diagnostic(payload)


def _route_template(scope):
    return getattr(scope.get("route"), "path", None) or "<unmatched>"


async def _safe_request(app, scope, receive, send, *, mark_unknown_outcome=False,
                        propagate_send_errors=False):
    started = False
    send_failed = False
    start = time.perf_counter()

    async def guarded_send(message):
        nonlocal started, send_failed
        if message["type"] == "http.response.start":
            started = True
        try:
            await send(message)
        except BaseException:
            send_failed = True
            raise

    try:
        await app(scope, receive, guarded_send)
    except Exception as exc:
        if propagate_send_errors and send_failed:
            # A downstream audit/transport failure belongs to the SaaS or
            # outer safe boundary. Do not swallow it as a business failure
            # after a response that might never have reached the transport.
            raise
        emit_diagnostic("http_failed_after_response" if started else "http_failed",
                        code="REQUEST_FAILED", error_type=safe_error_type(exc),
                        method=scope.get("method"), route=_route_template(scope),
                        usage_outcome=scope.get("state", {}).get("_usage_outcome"),
                        elapsed_ms=(time.perf_counter() - start) * 1000)
        if not started:
            if mark_unknown_outcome:
                # Private ASGI state, set only by the inner error boundary. A
                # fallback 500 is not a business decision rejecting the attempt.
                scope.setdefault("state", {})["_business_outcome_unknown"] = True
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
        await _safe_request(self.app, scope, receive, send, mark_unknown_outcome=True,
                            propagate_send_errors=True)


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
                                route=_route_template(scope), elapsed_ms=elapsed,
                                usage_outcome=scope.get("state", {}).get("_usage_outcome"))
            await send(message)

        with request_diagnostic_context(request_id):
            await _safe_request(self.app, scope, receive, traced_send)
