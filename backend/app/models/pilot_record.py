"""One self-reported pilot observation per content run, inside its tenant store."""
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PilotRecord(Base):
    __tablename__ = "pilot_records"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_pilot_version"),
        CheckConstraint("outcome IN ('pending','accepted','rejected','abandoned')", name="ck_pilot_outcome"),
        CheckConstraint("baseline_minutes IS NULL OR baseline_minutes > 0", name="ck_pilot_baseline"),
        CheckConstraint("actual_work_minutes IS NULL OR actual_work_minutes >= 0", name="ck_pilot_actual"),
        CheckConstraint("support_minutes IS NULL OR support_minutes >= 0", name="ck_pilot_support"),
    )

    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("agent_runs.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    cohort: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    baseline_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_work_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    support_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_reference: Mapped[str] = mapped_column(Text, nullable=False, default="")
    acceptance_reference: Mapped[str] = mapped_column(Text, nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="self_reported")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
