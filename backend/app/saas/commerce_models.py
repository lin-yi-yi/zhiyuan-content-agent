"""Control-plane settings and an auditable request-attempt ledger."""
from datetime import datetime, timezone
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.saas.store import ControlBase


def utcnow():
    return datetime.now(timezone.utc)


class OrganizationPlan(ControlBase):
    __tablename__ = "saas_organization_plans"
    organization_id: Mapped[str] = mapped_column(ForeignKey("saas_organizations.id"), primary_key=True)
    code: Mapped[str] = mapped_column(String(20), default="trial")
    limits: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OrganizationConnection(ControlBase):
    __tablename__ = "saas_organization_connections"
    organization_id: Mapped[str] = mapped_column(ForeignKey("saas_organizations.id"), primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    is_default: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UsageCounter(ControlBase):
    __tablename__ = "saas_usage_counters"
    __table_args__ = (CheckConstraint("reserved >= 0 AND settled >= 0", name="ck_usage_nonnegative"),)
    organization_id: Mapped[str] = mapped_column(ForeignKey("saas_organizations.id"), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    metric: Mapped[str] = mapped_column(String(30), primary_key=True)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    settled: Mapped[int] = mapped_column(Integer, default=0)


class UsageEvent(ControlBase):
    __tablename__ = "saas_usage_events"
    __table_args__ = (
        UniqueConstraint("organization_id", "request_hash", name="uq_usage_org_request"),
        CheckConstraint("amount > 0", name="ck_usage_positive"),
        CheckConstraint("status IN ('reserved','settled','refunded')", name="ck_usage_status"),
    )
    token: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    organization_id: Mapped[str] = mapped_column(ForeignKey("saas_organizations.id"), index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    period: Mapped[str] = mapped_column(String(7), index=True)
    metric: Mapped[str] = mapped_column(String(30))
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UpgradeRequest(ControlBase):
    __tablename__ = "saas_upgrade_requests"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    organization_id: Mapped[str] = mapped_column(ForeignKey("saas_organizations.id"), index=True)
    requested_by: Mapped[str] = mapped_column(String(32))
    requested_plan: Mapped[str] = mapped_column(String(20), default="team")
    note: Mapped[str] = mapped_column(String(1000), default="")
    status: Mapped[str] = mapped_column(String(20), default="requested")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
