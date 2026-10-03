"""模型调用记录表"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    input_preview: Mapped[str] = mapped_column(Text, nullable=True)
    output_preview: Mapped[str] = mapped_column(Text, nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    agent_step_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    step_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    workflow_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    invocation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    request_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    call_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(24, 12), nullable=True)
    cost_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    pricing_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cost_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
