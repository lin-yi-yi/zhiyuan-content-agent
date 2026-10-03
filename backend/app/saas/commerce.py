"""SQLite-atomic SaaS quota reservations; these count attempts, not token cost.

Reservations remain charged until finalized. A process crash does not silently
refund an in-flight task; operators must reconcile it instead of risking excess
usage. An identical request ID is rejected, not re-executed or charged twice.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import uuid

from sqlalchemy import func, select, text

from app.saas import store
from app.saas.commerce_models import OrganizationPlan, UpgradeRequest, UsageCounter, UsageEvent


TRIAL_LIMITS = {"ai_requests": 100, "documents": 200, "members": 3}


class CommerceError(ValueError):
    pass


class UsageConflict(CommerceError):
    pass


class QuotaExceeded(CommerceError):
    def __init__(self, *, metric, limit, used, period):
        super().__init__("本组织当前周期的 AI 请求额度已用完。")
        self.metric, self.limit, self.used, self.period = metric, limit, used, period


def _now():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat() if value else None


def _period(now=None):
    now = now or _now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return {"label": start.strftime("%Y-%m"), "start": _iso(start), "end": _iso(end)}


@contextmanager
def atomic_session():
    """A fresh transaction: do not share the identity middleware's read session."""
    with store.session_factory() as db:
        if db.get_bind().dialect.name != "sqlite":
            raise RuntimeError("The current control-plane ledger requires SQLite.")
        try:
            db.execute(text("BEGIN IMMEDIATE"))
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise


def get_plan_limits(organization_id, *, db=None):
    if db is None:
        with store.session_factory() as session:
            return get_plan_limits(organization_id, db=session)
    plan = db.get(OrganizationPlan, organization_id)
    if not plan or plan.code == "trial":
        return dict(TRIAL_LIMITS)
    if plan.code != "team" or not isinstance(plan.limits, dict) or set(plan.limits) != set(TRIAL_LIMITS):
        raise CommerceError("套餐配置不可用，请联系服务管理员。")
    if any(type(value) is not int or value < 0 for value in plan.limits.values()):
        raise CommerceError("套餐配置不可用，请联系服务管理员。")
    return dict(plan.limits)


def provision_plan(organization_id, *, code, limits=None):
    """Operator-only helper; there is deliberately no public entitlement endpoint."""
    if code not in {"trial", "team"}:
        raise CommerceError("不支持的套餐。")
    if code == "team" and (not isinstance(limits, dict) or set(limits) != set(TRIAL_LIMITS)
                           or any(type(value) is not int or value < 0 for value in limits.values())):
        raise CommerceError("团队套餐必须由运营人员明确配置全部额度。")
    with atomic_session() as db:
        plan = db.get(OrganizationPlan, organization_id)
        if not plan:
            plan = OrganizationPlan(organization_id=organization_id)
            db.add(plan)
        plan.code, plan.limits, plan.updated_at = code, dict(limits or TRIAL_LIMITS), _now()
    return {"code": code, "limits": dict(limits or TRIAL_LIMITS) if code == "team" else dict(TRIAL_LIMITS)}


def reserve_usage(organization_id, request_id, metric="ai_requests", amount=1):
    """Return an internal opaque token, or reject before a task can execute."""
    if (not isinstance(organization_id, str) or not organization_id
            or not isinstance(request_id, str) or not 1 <= len(request_id) <= 200
            or metric != "ai_requests" or type(amount) is not int or not 1 <= amount <= 1000):
        raise CommerceError("无效的用量预留参数。")
    request_hash = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    period, token = _period()["label"], uuid.uuid4().hex
    with atomic_session() as db:
        if db.scalar(select(UsageEvent.token).where(UsageEvent.organization_id == organization_id,
                                                   UsageEvent.request_hash == request_hash)):
            raise UsageConflict("该请求已登记，请使用新的请求标识重试。")
        limit = get_plan_limits(organization_id, db=db)[metric]
        counter = db.get(UsageCounter, (organization_id, period, metric))
        used = counter.reserved + counter.settled if counter else 0
        if used + amount > limit:
            raise QuotaExceeded(metric=metric, limit=limit, used=used, period=period)
        if not counter:
            counter = UsageCounter(organization_id=organization_id, period=period, metric=metric, reserved=0, settled=0)
            db.add(counter)
        counter.reserved += amount
        db.add(UsageEvent(token=token, organization_id=organization_id, request_hash=request_hash,
                          period=period, metric=metric, amount=amount, status="reserved"))
    return token


def finalize_usage(token, succeeded: bool):
    if not isinstance(token, str) or len(token) != 32 or type(succeeded) is not bool:
        raise CommerceError("无效的用量完成参数。")
    with atomic_session() as db:
        event = db.get(UsageEvent, token)
        if not event:
            raise CommerceError("用量预留不存在。")
        if event.status == "reserved":
            counter = db.get(UsageCounter, (event.organization_id, event.period, event.metric))
            if not counter or counter.reserved < event.amount:
                raise CommerceError("用量账本需要管理员检查。")
            counter.reserved -= event.amount
            if succeeded:
                counter.settled += event.amount
            event.status, event.completed_at = ("settled" if succeeded else "refunded"), _now()
        result = {"status": event.status, "metric": event.metric, "amount": event.amount, "period": event.period}
    return result


def _upgrade_view(request):
    return {"id": request.id, "requested_plan": request.requested_plan,
            "status": request.status, "created_at": _iso(request.created_at)}


def request_upgrade(organization_id, user_id, *, requested_plan="team", note=""):
    if requested_plan != "team" or not isinstance(note, str) or len(note) > 1000:
        raise CommerceError("仅支持登记团队套餐需求，说明最多 1000 字。")
    with atomic_session() as db:
        # Repeated clicks return the existing open request, never grant rights.
        request = db.scalar(select(UpgradeRequest).where(UpgradeRequest.organization_id == organization_id,
                            UpgradeRequest.status == "requested").order_by(UpgradeRequest.created_at.desc()))
        if not request:
            request = UpgradeRequest(organization_id=organization_id, requested_by=user_id,
                                     requested_plan=requested_plan, note=note.strip())
            db.add(request)
            db.flush()
        result = _upgrade_view(request)
    return result


def billing_snapshot(organization_id, *, document_count=None):
    from app.saas.models import Membership

    period = _period()
    with store.session_factory() as db:
        limits = get_plan_limits(organization_id, db=db)
        plan = db.get(OrganizationPlan, organization_id)
        code = plan.code if plan else "trial"
        counter = db.get(UsageCounter, (organization_id, period["label"], "ai_requests"))
        reserved, settled = (counter.reserved, counter.settled) if counter else (0, 0)
        events = db.scalars(select(UsageEvent).where(UsageEvent.organization_id == organization_id,
                            UsageEvent.period == period["label"]).order_by(UsageEvent.created_at.desc()).limit(20)).all()
        refunded = db.scalar(select(func.coalesce(func.sum(UsageEvent.amount), 0)).where(
            UsageEvent.organization_id == organization_id, UsageEvent.period == period["label"], UsageEvent.status == "refunded"))
        members = db.scalar(select(func.count()).select_from(Membership).where(
            Membership.organization_id == organization_id, Membership.is_active.is_(True)))
        requests = db.scalars(select(UpgradeRequest).where(UpgradeRequest.organization_id == organization_id)
                              .order_by(UpgradeRequest.created_at.desc()).limit(20)).all()
        return {
            "plan": {"code": code, "name": "试用套餐" if code == "trial" else "团队套餐"},
            "limits": limits, "period": period,
            "usage": {"ai_requests": {"used": reserved + settled, "reserved": reserved, "settled": settled,
                       "remaining": max(0, limits["ai_requests"] - reserved - settled),
                       "attempts": reserved + settled + refunded, "refunded": refunded},
                      "documents": {"used": document_count, "limit": limits["documents"], "measured": document_count is not None},
                      "members": {"used": members, "limit": limits["members"]}},
            "upgrade_requests": [_upgrade_view(request) for request in requests],
            "usage_events": [{"request_ref": event.request_hash[:12], "metric": event.metric, "amount": event.amount,
                              "status": event.status, "created_at": _iso(event.created_at),
                              "completed_at": _iso(event.completed_at)} for event in events],
            "notice": "AI 用量按 HTTP 受理尝试计次，包含未完成预留；HTTP 请求失败退回，已受理的后台任务后来失败不自动退回。这不是 token 数量或模型费用。升级申请只登记需求，不收款或自动提升权益。",
        }
