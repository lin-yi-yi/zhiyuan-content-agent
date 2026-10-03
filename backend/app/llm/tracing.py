"""Model-call metadata only: task context, explicit attempts, and configured estimates.

No raw prompts, model responses, keys, provider error bodies or invoice claims are
persisted here. A log row is one application-level request (SDK retries disabled).
"""
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import timezone
from decimal import Decimal, DecimalException, InvalidOperation, localcontext
import hashlib
import json
import re
from uuid import uuid4

from app.core.config import settings


@dataclass(frozen=True)
class ModelTrace:
    agent_run_id: int | None = None
    agent_step_id: int | None = None
    step_key: str | None = None
    workflow_attempt: int | None = None


@dataclass(frozen=True)
class ModelInvocation:
    invocation_id: str
    request_index: int = 0


current_model_trace: ContextVar[ModelTrace | None] = ContextVar("current_model_trace", default=None)
_current_invocation: ContextVar[ModelInvocation | None] = ContextVar("current_model_invocation", default=None)
MAX_LOGGED_TOKENS = 2_147_483_647  # Portable SQL INTEGER bound; invalid SDK values remain unknown.


@contextmanager
def model_trace_context(agent_run_id=None, agent_step_id=None, step_key=None, workflow_attempt=None):
    token = current_model_trace.set(ModelTrace(agent_run_id, agent_step_id, step_key, workflow_attempt))
    try:
        yield current_model_trace.get()
    finally:
        current_model_trace.reset(token)


@contextmanager
def model_invocation_context():
    """A chat_json call owns all of its format fallback and repair requests."""
    token = _current_invocation.set(ModelInvocation(uuid4().hex))
    try:
        yield
    finally:
        _current_invocation.reset(token)


def model_request_metadata(system_prompt: str, call_kind: str = "initial") -> dict:
    trace = current_model_trace.get() or ModelTrace()
    invocation = _current_invocation.get()
    if invocation is None:
        invocation = ModelInvocation(uuid4().hex, 1)
    else:
        invocation = replace(invocation, request_index=invocation.request_index + 1)
        _current_invocation.set(invocation)
    return {
        "agent_run_id": trace.agent_run_id, "agent_step_id": trace.agent_step_id,
        "step_key": trace.step_key, "workflow_attempt": trace.workflow_attempt,
        "invocation_id": invocation.invocation_id, "request_index": invocation.request_index,
        "call_kind": call_kind,
        "prompt_version": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest(),
    }


def usage_counts(usage) -> dict:
    def count(name):
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        return value if type(value) is int and 0 <= value <= MAX_LOGGED_TOKENS else None
    return {key: count(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}


def _configured_price(provider: str, model: str):
    raw = settings.MODEL_PRICING_JSON
    if raw is None or raw == "":
        return None, "unpriced"
    try:
        if not isinstance(raw, str) or len(raw) > 100_000:
            raise ValueError("Invalid price configuration")
        if not raw.strip():
            return None, "unpriced"
        def invalid_constant(_):
            raise ValueError("Non-finite price")
        entries = json.loads(raw, parse_float=Decimal, parse_constant=invalid_constant)
        if not isinstance(entries, list) or len(entries) > 100:
            raise ValueError("Invalid price configuration")
        parsed = []
        for item in entries:
            if not isinstance(item, dict) or set(item) != {
                "provider", "model", "version", "currency", "input_per_million", "output_per_million",
            }:
                raise ValueError("Invalid price entry")
            for key, length in (("provider", 50), ("model", 100), ("version", 100)):
                if not isinstance(item[key], str) or not item[key].strip() or len(item[key]) > length:
                    raise ValueError("Invalid price label")
            if not isinstance(item["currency"], str) or not re.fullmatch(r"[A-Z]{3}", item["currency"]):
                raise ValueError("Invalid currency")
            prices = {}
            for key in ("input_per_million", "output_per_million"):
                value = item[key]
                if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
                    raise ValueError("Invalid token rate")
                rate = Decimal(str(value))
                if not rate.is_finite() or rate < 0 or rate > Decimal("1000000000"):
                    raise ValueError("Invalid token rate")
                prices[key] = rate
            parsed.append({**item, **prices})
        matching = [entry for entry in parsed if entry["provider"] == provider and entry["model"] == model]
        if len(matching) > 1:
            raise ValueError("Duplicate price key")
        return (matching[0], "estimated") if matching else (None, "unpriced")
    except (ValueError, TypeError, DecimalException):
        return None, "invalid_pricing"


def estimate_model_cost(provider: str, model: str, success: bool, prompt_tokens, completion_tokens) -> dict:
    empty = {"estimated_cost": None, "cost_currency": None, "pricing_version": None}
    if provider == "local":
        return {**empty, "cost_status": "local_rule"}
    if not success:
        return {**empty, "cost_status": "failed"}
    price, status = _configured_price(provider, model)
    if price is None:
        return {**empty, "cost_status": status}
    reference = {"cost_currency": price["currency"], "pricing_version": price["version"]}
    if any(type(value) is not int or not 0 <= value <= MAX_LOGGED_TOKENS for value in (prompt_tokens, completion_tokens)):
        return {**empty, **reference, "cost_status": "missing_usage"}
    try:
        with localcontext() as ctx:
            ctx.prec = 40
            cost = (Decimal(prompt_tokens) * price["input_per_million"]
                    + Decimal(completion_tokens) * price["output_per_million"]) / Decimal(1000000)
            if not cost.is_finite() or cost >= Decimal("1000000000000"):
                raise InvalidOperation
            stored = cost.quantize(Decimal("0.000000000001"))
            positive_cost = (prompt_tokens > 0 and price["input_per_million"] > 0
                             or completion_tokens > 0 and price["output_per_million"] > 0)
            if positive_cost and stored == 0:
                raise InvalidOperation
    except (DecimalException, ValueError, OverflowError):
        return {**empty, **reference, "cost_status": "invalid_pricing"}
    return {**reference, "estimated_cost": stored, "cost_status": "estimated"}


def persist_model_run(*, task_type, provider, model_name, prompt_hash, success, latency_ms,
                      request_metadata, usage=None, error_type=None):
    """Best-effort independent metadata write; failure never exposes model payloads."""
    from app.db.session import SessionLocal
    from app.models.model_run import ModelRun
    db = None
    try:
        counts = usage_counts(usage)
        try:
            cost = estimate_model_cost(provider, model_name, success, counts["prompt_tokens"], counts["completion_tokens"])
        except Exception:
            # Estimation must never turn a successful provider request into a failure.
            cost = {"estimated_cost": None, "cost_currency": None, "pricing_version": None,
                    "cost_status": "invalid_pricing"}
        db = SessionLocal()
        db.add(ModelRun(
            task_type=task_type, provider=provider, model_name=model_name,
            input_preview=None, output_preview=None, error_message=None,
            prompt_hash=prompt_hash, success=success, latency_ms=latency_ms,
            error_type=error_type, **counts, **request_metadata, **cost,
        ))
        db.commit()
    except Exception:
        if db is not None:
            with suppress(Exception):
                db.rollback()
    finally:
        if db is not None:
            with suppress(Exception):
                db.close()


def serialize_model_run(row) -> dict:
    """Whitelist safe fields; legacy preview/error columns never reach the API."""
    keys = (
        "id", "task_type", "provider", "model_name", "agent_run_id", "agent_step_id",
        "step_key", "workflow_attempt", "invocation_id", "request_index", "call_kind",
        "prompt_hash", "prompt_version", "prompt_tokens", "completion_tokens", "total_tokens",
        "success", "error_type", "latency_ms", "cost_currency", "pricing_version",
    )
    output = {key: getattr(row, key, None) for key in keys}
    amount = getattr(row, "estimated_cost", None)
    output["estimated_cost"] = format(amount, "f") if amount is not None else None
    output["cost_status"] = getattr(row, "cost_status", None) or "legacy_unknown"
    output["cost_basis"] = "configured_token_estimate" if output["cost_status"] == "estimated" else None
    output["cost_is_invoice"] = False
    created_at = getattr(row, "created_at", None)
    output["created_at"] = created_at.replace(tzinfo=timezone.utc).isoformat() if created_at and created_at.tzinfo is None else (created_at.isoformat() if created_at else None)
    return output
