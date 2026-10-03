from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ContentPredictionCreate(BaseModel):
    draft_id: int
    publish_log_id: Optional[int] = None
    platform: str = "xiaohongshu"
    predicted_views: int = Field(0, ge=0)
    predicted_likes: int = Field(0, ge=0)
    predicted_favorites: int = Field(0, ge=0)
    predicted_comments: int = Field(0, ge=0)
    predicted_shares: int = Field(0, ge=0)
    predicted_new_followers: int = Field(0, ge=0)
    predicted_save_rate: Optional[float] = None
    predicted_like_rate: Optional[float] = None
    predicted_comment_rate: Optional[float] = None
    predicted_follow_conversion_rate: Optional[float] = None
    confidence: int = Field(60, ge=0, le=100)
    rubric_version: str = "manual-v1"
    rationale: Optional[str] = None
    risk_notes: Optional[str] = None
    status: Literal["draft", "locked", "settled"] = "draft"


class ContentPredictionUpdate(BaseModel):
    publish_log_id: Optional[int] = None
    platform: Optional[str] = None
    predicted_views: Optional[int] = Field(None, ge=0)
    predicted_likes: Optional[int] = Field(None, ge=0)
    predicted_favorites: Optional[int] = Field(None, ge=0)
    predicted_comments: Optional[int] = Field(None, ge=0)
    predicted_shares: Optional[int] = Field(None, ge=0)
    predicted_new_followers: Optional[int] = Field(None, ge=0)
    predicted_save_rate: Optional[float] = None
    predicted_like_rate: Optional[float] = None
    predicted_comment_rate: Optional[float] = None
    predicted_follow_conversion_rate: Optional[float] = None
    confidence: Optional[int] = Field(None, ge=0, le=100)
    rubric_version: Optional[str] = None
    rationale: Optional[str] = None
    risk_notes: Optional[str] = None
    status: Optional[Literal["draft", "locked", "settled"]] = None


class PredictionAttachLog(BaseModel):
    publish_log_id: int


class ContentPredictionOut(BaseModel):
    id: int
    draft_id: int
    publish_log_id: Optional[int] = None
    platform: str
    predicted_views: int
    predicted_likes: int
    predicted_favorites: int
    predicted_comments: int
    predicted_shares: int
    predicted_new_followers: int
    predicted_save_rate: Optional[float] = None
    predicted_like_rate: Optional[float] = None
    predicted_comment_rate: Optional[float] = None
    predicted_follow_conversion_rate: Optional[float] = None
    confidence: int
    rubric_version: str
    rationale: Optional[str] = None
    risk_notes: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}
