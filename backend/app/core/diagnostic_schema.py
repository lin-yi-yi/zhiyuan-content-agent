"""Versioned, standard-library-only diagnostic metadata shared with offline tools.

Validation never coerces arbitrary objects or returns partially trusted records.
Routes must originate from registered templates, not client URLs; syntax checks
are defense in depth and cannot determine whether a caller used that source.
"""
from datetime import datetime
import math
import re

MAX_RECORD_BYTES = 4096
EVENTS = frozenset({
    "http_response", "http_failed", "http_failed_after_response", "saas_boundary_failed",
    "workflow_started", "workflow_finished", "workflow_step_failed", "workflow_background_failed",
    "usage_finalization_failed", "usage_outcome_unknown",
})
CODES = frozenset({"REQUEST_FAILED", "BACKGROUND_FAILED", "INSUFFICIENT_EVIDENCE", "INVALID_CITATIONS",
    "WORKFLOW_CONFLICT", "RETRIEVAL_FAILED", "MODEL_CALL_FAILED", "STEP_FAILED",
    "USAGE_FINALIZATION_UNCONFIRMED", "USAGE_OUTCOME_UNKNOWN"})
ERROR_TYPES = frozenset({
    "ValueError", "TypeError", "RuntimeError", "OSError", "TimeoutError", "ConnectionError",
    "PermissionError", "FileNotFoundError", "OperationalError", "IntegrityError", "StatementError",
    "SQLAlchemyError", "ConnectionUnavailable", "ModelCallError", "RetrievalError", "EvidenceError",
    "CitationError", "WorkflowConflict", "WorkflowCancelled", "UnexpectedError",
})
STEPS = frozenset({"retrieve_context", "topic_ideas", "create_topic", "score_topic", "generate_draft",
    "generate_cards", "compliance_check", "evaluate_package", "revise_package", "reevaluate_package",
    "agent_decision", "human_review", "queued", "cancelled"})
STATUSES = frozenset({"pending", "running", "failed", "cancelled", "awaiting_review", "approved", "rejected"})
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
USAGE_OUTCOMES = frozenset({"accepted", "rejected", "unknown"})
FIELDS = frozenset({"schema_version", "event", "at", "request_id", "organization_id", "agent_run_id",
    "agent_step_id", "workflow_attempt", "step_key", "status", "code", "error_type", "elapsed_ms",
    "http_status", "usage_outcome", "method", "route"})


def valid_request_id(value):
    return value if type(value) is str and re.fullmatch(r"[a-f0-9]{32}", value) else None


def safe_route_template(value):
    if type(value) is not str:
        return None
    if value == "<unmatched>":
        return value
    segment = r"(?:[A-Za-z0-9_.-]+|\{[A-Za-z_][A-Za-z0-9_]*(?::[A-Za-z_][A-Za-z0-9_]*)?\})"
    if (len(value) <= 256 and re.fullmatch(rf"/(?:{segment}(?:/{segment})*/?)?", value)
            and not any(part in {".", ".."} for part in value.split("/"))):
        return value
    return None


def validate_record(record):
    """Return a validated copy or None; no I/O, app imports, or exception text."""
    if type(record) is not dict or set(record) != FIELDS:
        return None
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        return None
    if type(record["event"]) is not str or record["event"] not in EVENTS:
        return None
    timestamp = record["at"]
    if type(timestamp) is not str or not 20 <= len(timestamp) <= 40:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
    except ValueError:
        return None
    for name in ("request_id", "organization_id"):
        if record[name] is not None and valid_request_id(record[name]) is None:
            return None
    for name in ("agent_run_id", "agent_step_id", "workflow_attempt"):
        value = record[name]
        if value is not None and (type(value) is not int or not 0 < value < 2**63):
            return None
    for name, options in (("step_key", STEPS), ("status", STATUSES), ("code", CODES),
                          ("error_type", ERROR_TYPES), ("usage_outcome", USAGE_OUTCOMES), ("method", METHODS)):
        value = record[name]
        if value is not None and (type(value) is not str or value not in options):
            return None
    elapsed = record["elapsed_ms"]
    if elapsed is not None and (type(elapsed) not in {int, float} or elapsed < 0):
        return None
    try:
        if elapsed is not None and not math.isfinite(elapsed):
            return None
    except OverflowError:
        return None
    status = record["http_status"]
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        return None
    if record["route"] is not None and safe_route_template(record["route"]) is None:
        return None
    return dict(record)
