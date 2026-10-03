"""Versioned brand instructions, scoped to one workspace and knowledge base."""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class BrandProfile(Base):
    __tablename__ = "brand_profiles"
    __table_args__ = (
        CheckConstraint("data_policy IN ('cloud_allowed', 'local_only')", name="ck_brand_data_policy"),
        CheckConstraint("version >= 1", name="ck_brand_version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    knowledge_base_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    audience: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tone: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prohibited_claims: Mapped[str] = mapped_column(Text, nullable=False, default="")
    call_to_action: Mapped[str] = mapped_column(Text, nullable=False, default="")
    data_policy: Mapped[str] = mapped_column(String(30), nullable=False, default="cloud_allowed")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
