from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, field_validator


class PublishLogCreate(BaseModel):
    draft_id: int; platform: str
    published_at: Optional[datetime] = None; post_url: Optional[str] = None
    used_title: Optional[str] = None; used_cover_text: Optional[str] = None
    content_type: Optional[str] = None; notes: Optional[str] = None


class PublishLogOut(BaseModel):
    id: int; draft_id: int; platform: str
    published_at: Optional[datetime] = None; post_url: Optional[str] = None
    used_title: Optional[str] = None; used_cover_text: Optional[str] = None
    content_type: Optional[str] = None; notes: Optional[str] = None
    created_at: datetime; updated_at: datetime
    model_config = {"from_attributes": True}

    @field_validator("published_at", "created_at", "updated_at")
    @classmethod
    def timestamps_in_utc(cls, value):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
