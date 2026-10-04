"""Local operator reconciliation of uncertain HTTP-attempt reservations.

There is deliberately no public route and no timeout-based refund. The CLI
requires an offline lock; this service only owns the atomic ledger/audit change.
"""
import hashlib
import json
import re

from sqlalchemy import func, select

from app.saas.commerce import CommerceError, UsageConflict, _iso, _now, atomic_session
from app.saas.commerce_models import UsageCounter, UsageEvent
from app.saas.models import AuditLog, Organization


ACTION = "usage.reconciled"
DECISIONS = {"confirmed_accepted": "settled", "confirmed_not_accepted": "refunded"}


def _label(value, name, maximum):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise CommerceError(f"{name}不能为空、超长或包含控制字符。")
    return value.strip()


def reconcile_usage(organization_id, request_hash, *, operation_id, decision,
                    operator_label, operator_uid, reason, evidence_ref, apply=False):
    """Preview by default; exact retries reuse one audit row and never reapply.

    operator_label is a declared operator reference, not a logged-in SaaS user.
    The CLI supplies the OS effective UID; neither is authentication over HTTP.
    """
    for value, pattern in ((organization_id, r"[a-f0-9]{32}"),
                           (request_hash, r"[a-f0-9]{64}"),
                           (operation_id, r"[a-f0-9]{32}")):
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise CommerceError("组织、完整请求哈希或操作标识无效。")
    if not isinstance(decision, str) or decision not in DECISIONS or type(apply) is not bool:
        raise CommerceError("必须明确确认请求已受理或未受理；不确定时保留预留。")
    if type(operator_uid) is not int or operator_uid < 0:
        raise CommerceError("操作系统用户标识无效。")
    operator_label = _label(operator_label, "操作者标识", 80)
    reason = _label(reason, "处理理由", 300)
    evidence_ref = _label(evidence_ref, "核对证据编号", 160)
    inputs = {"organization_id": organization_id, "request_hash": request_hash,
              "operation_id": operation_id, "decision": decision, "operator_label": operator_label,
              "operator_uid": operator_uid, "reason": reason, "evidence_ref": evidence_ref}
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with atomic_session() as db:
        previous = db.get(AuditLog, operation_id)
        if previous:
            details = previous.details_json if isinstance(previous.details_json, dict) else {}
            if (previous.action != ACTION or previous.organization_id != organization_id
                    or details.get("input_sha256") != fingerprint or not isinstance(details.get("result"), dict)):
                raise UsageConflict("操作标识已用于不同处理，请核对原审计记录。")
            return {**details["result"], "mode": "replayed"}
        if not db.get(Organization, organization_id):
            raise CommerceError("组织不存在。")
        event = db.scalar(select(UsageEvent).where(UsageEvent.organization_id == organization_id,
                                                  UsageEvent.request_hash == request_hash))
        if not event:
            raise CommerceError("该组织内没有匹配的完整请求哈希。")
        if event.status != "reserved":
            raise UsageConflict("预留已完成，不能再次处理；原操作重试须使用原操作标识和参数。")
        if event.metric != "ai_requests" or event.amount <= 0:
            raise CommerceError("用量事件异常，请先检查账本。")
        counter = db.get(UsageCounter, (organization_id, event.period, event.metric))
        totals = dict(db.execute(select(UsageEvent.status, func.sum(UsageEvent.amount)).where(
            UsageEvent.organization_id == organization_id, UsageEvent.period == event.period,
            UsageEvent.metric == event.metric).group_by(UsageEvent.status)).all())
        if (not counter or counter.reserved != totals.get("reserved", 0)
                or counter.settled != totals.get("settled", 0)):
            raise CommerceError("用量计数与事件不一致，请先检查账本；本工具不自动修复。")
        result = {"operation_id": operation_id, "organization_id": organization_id,
                  "request_ref": request_hash, "period": event.period, "metric": event.metric,
                  "amount": event.amount, "before": event.status, "after": DECISIONS[decision],
                  "reserved_after": counter.reserved - event.amount,
                  "settled_after": counter.settled + (event.amount if decision == "confirmed_accepted" else 0),
                  "mode": "applied" if apply else "preview",
                  "notice": "仅处理 HTTP 受理尝试额度，不执行资金退款，也不修改模型 token 或供应商账单。"}
        if apply:
            counter.reserved = result["reserved_after"]
            counter.settled = result["settled_after"]
            event.status, event.completed_at = result["after"], _now()
            db.add(AuditLog(id=operation_id, organization_id=organization_id, actor_user_id=None,
                action=ACTION, resource=f"usage:{request_hash}", created_at=event.completed_at,
                details_json={"input_sha256": fingerprint, "decision": decision,
                    "operator_label": operator_label, "operator_uid": operator_uid,
                    "identity_source": "local_cli_declared_label_and_os_euid",
                    "reason": reason, "evidence_ref": evidence_ref, "result": result,
                    "reservation_created_at": _iso(event.created_at)}))
    return result
