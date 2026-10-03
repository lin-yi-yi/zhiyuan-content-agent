"""发布前预测表。"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ContentPrediction(Base):
    __tablename__ = "content_predictions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("drafts.id", ondelete="CASCADE"), nullable=False)
    publish_log_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("publish_logs.id", ondelete="SET NULL"), nullable=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=False, default="xiaohongshu")
    predicted_views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    predicted_likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    predicted_favorites: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    predicted_comments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    predicted_shares: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    predicted_new_followers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    predicted_save_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=True)
    predicted_like_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=True)
    predicted_comment_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=True)
    predicted_follow_conversion_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    rubric_version: Mapped[str] = mapped_column(String(100), nullable=False, default="manual-v1")
    rationale: Mapped[str] = mapped_column(Text, nullable=True)
    risk_notes: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
