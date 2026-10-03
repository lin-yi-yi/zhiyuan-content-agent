from datetime import datetime
import math
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class MetricCreate(BaseModel):
    publish_log_id: Optional[int] = None
    views: int = Field(0, ge=0)
    likes: int = Field(0, ge=0)
    favorites: int = Field(0, ge=0)
    comments: int = Field(0, ge=0)
    shares: int = Field(0, ge=0)
    new_followers: int = Field(0, ge=0)
    impressions: Optional[int] = Field(None, ge=0)
    click_rate: Optional[float] = Field(None, ge=0, le=1, allow_inf_nan=False)
    profile_visits: Optional[int] = Field(None, ge=0)
    follow_conversion_rate: Optional[float] = Field(None, ge=0, le=1, allow_inf_nan=False)
    notes: Optional[str] = None

    @field_validator(
        "views", "likes", "favorites", "comments", "shares", "new_followers",
        "impressions", "profile_visits", "click_rate", "follow_conversion_rate", mode="before",
    )
    @classmethod
    def json_safe_invalid_number(cls, value):
        # Reject non-finite numbers below, with a JSON-safe input in the 422 error.
        # Python's JSON parser also accepts NaN/Infinity and overflows such as 1e999.
        return str(value) if isinstance(value, float) and not math.isfinite(value) else value


class MetricOut(BaseModel):
    id: int; publish_log_id: int
    views: int; likes: int; favorites: int; comments: int; shares: int
    new_followers: int; impressions: Optional[int] = None
    click_rate: Optional[float] = None; profile_visits: Optional[int] = None
    follow_conversion_rate: Optional[float] = None
    collected_at: datetime; notes: Optional[str] = None
    model_config = {"from_attributes": True}
