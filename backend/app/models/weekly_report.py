"""7天复盘表"""
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, JSON, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class WeeklyReport(Base):
    __tablename__ = "weekly_reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    report_text: Mapped[str] = mapped_column(Text, nullable=False)
    best_topics: Mapped[dict] = mapped_column(JSON, nullable=True)
    worst_topics: Mapped[dict] = mapped_column(JSON, nullable=True)
    angle_performance: Mapped[dict] = mapped_column(JSON, nullable=True)
    content_type_performance: Mapped[dict] = mapped_column(JSON, nullable=True)
    template_performance: Mapped[dict] = mapped_column(JSON, nullable=True)
    performance_summary: Mapped[dict] = mapped_column(JSON, nullable=True)
    prediction_calibration: Mapped[dict] = mapped_column(JSON, nullable=True)
    recommendations: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
