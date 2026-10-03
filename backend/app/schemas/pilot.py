"""Pilot inputs are observations, never inferred from model scores or elapsed time."""
from datetime import date, datetime, timezone
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PilotRecordInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    version: int = Field(ge=0, strict=True)
    cohort: str = Field(default="首轮试点", min_length=1, max_length=80)
    outcome: Literal["pending", "accepted", "rejected", "abandoned"] = "pending"
    baseline_minutes: float | None = Field(default=None, ge=0.01, le=100000, allow_inf_nan=False, strict=True)
    actual_work_minutes: float | None = Field(default=None, ge=0, le=100000, allow_inf_nan=False, strict=True)
    support_minutes: float | None = Field(default=None, ge=0, le=100000, allow_inf_nan=False, strict=True)
    baseline_reference: str = Field(default="", max_length=500)
    acceptance_reference: str = Field(default="", max_length=500)
    note: str = Field(default="", max_length=1000)

    @field_validator("baseline_minutes", "actual_work_minutes", "support_minutes", mode="before")
    @classmethod
    def finite_json_error(cls, value):
        # Preserve a JSON-safe 422 even for Python's accepted NaN/Infinity syntax.
        return str(value) if isinstance(value, float) and not math.isfinite(value) else value

    @model_validator(mode="after")
    def require_observation_references(self):
        if self.baseline_minutes is not None and not self.baseline_reference:
            raise ValueError("填写人工基线工时后，必须说明基线来源")
        if self.outcome == "accepted" and not self.acceptance_reference:
            raise ValueError("记录验收通过时，必须填写验收依据")
        return self


class PilotRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: int
    version: int
    cohort: str
    outcome: Literal["pending", "accepted", "rejected", "abandoned"]
    baseline_minutes: float | None
    actual_work_minutes: float | None
    support_minutes: float | None
    baseline_reference: str
    acceptance_reference: str
    note: str
    content_hash: str | None
    source: Literal["self_reported"]
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_timestamps(cls, value):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class PilotRecordResponse(BaseModel):
    run_id: int
    record: PilotRecordOut | None


class PilotReportItem(BaseModel):
    run_id: int
    goal: str
    status: str
    created_at: datetime
    record: PilotRecordOut | None
    acceptance_current: bool | None

    @field_validator("created_at")
    @classmethod
    def utc_timestamp(cls, value):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class PilotReport(BaseModel):
    start_date: date
    end_date: date
    total_runs: int
    recorded_runs: int
    missing_records: int
    accepted_runs: int
    stale_acceptances: int
    rejected_runs: int
    abandoned_runs: int
    pending_runs: int
    comparable_runs: int
    baseline_minutes_total: float | None
    actual_minutes_total: float | None
    saved_minutes_total: float | None
    savings_rate: float | None
    items: list[PilotReportItem]
