"""Agent 执行记录 Schema"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.card import CardOut
from app.schemas.draft import DraftOut
from app.schemas.evidence import RequiredFacts
from app.schemas.topic import TopicOut


class AgentRunCreate(BaseModel):
    goal: str = Field(..., min_length=1, max_length=1000)
    mode: str = "inspiration"
    research_depth: str = "quick"
    target_audience: str = ""
    viewpoint: str = ""
    personal_case: str = ""
    content_type: str = "auto"
    source_urls: list[str] = []
    provider: str = "local"
    model: str = ""
    auto_score: bool = True
    use_rag: bool = False
    workspace_id: int | None = None
    knowledge_base_id: int | None = None
    rag_top_k: int = Field(5, ge=1, le=12)
    rag_min_score: float = Field(0.08, ge=0, le=1)
    required_facts: RequiredFacts = Field(default_factory=list)
    brand_profile_id: int | None = Field(default=None, ge=1)
    workflow_key: Literal["knowledge_post", "product_faq", "case_story"] | None = None

    @field_validator("goal")
    @classmethod
    def non_blank_goal(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("任务目标不能为空")
        return value.strip()

    @model_validator(mode="after")
    def facts_require_retrieval(self):
        if self.required_facts and not self.use_rag:
            raise ValueError("指定必需产品参数时必须启用知识库检索")
        return self


class AgentReviewCreate(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = Field("", max_length=2000)


class AgentStepOut(BaseModel):
    id: int
    run_id: int
    step_index: int
    key: str
    label: str
    status: str
    input_json: dict | None = None
    output_json: dict | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentRunOut(BaseModel):
    id: int
    goal: str
    mode: str
    provider: str
    model_name: str | None = None
    status: str
    current_step: str | None = None
    selected_topic_id: int | None = None
    draft_id: int | None = None
    evaluation_score: int | None = None
    result_json: dict | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    steps: list[AgentStepOut] = []

    model_config = {"from_attributes": True}


class AgentRunResult(AgentRunOut):
    topic: TopicOut | None = None
    draft: DraftOut | None = None
    cards: list[CardOut] = []
    evaluation: dict | None = None
