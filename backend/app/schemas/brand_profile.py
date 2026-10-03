"""Bounded brand configuration; factual source material belongs in knowledge."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BrandProfileCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    audience: str = Field(default="", max_length=1000)
    tone: str = Field(default="", max_length=1000)
    prohibited_claims: str = Field(default="", max_length=2000)
    call_to_action: str = Field(default="", max_length=1000)
    knowledge_base_id: int | None = Field(default=None, ge=1)
    workspace_id: int | None = Field(default=None, ge=1)
    data_policy: Literal["cloud_allowed", "local_only"] = "cloud_allowed"
    is_active: bool = True


class BrandProfileUpdate(BrandProfileCreate):
    version: int = Field(ge=1)


class BrandProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    audience: str
    tone: str
    prohibited_claims: str
    call_to_action: str
    knowledge_base_id: int
    workspace_id: int
    data_policy: Literal["cloud_allowed", "local_only"]
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime
