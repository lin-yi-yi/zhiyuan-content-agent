"""Question spans and unconfirmed scope proposals, never answer requirements."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, field_validator


Label = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
SpanText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=1000)]
IssueCode = Literal[
    "missing_product", "missing_parameter", "ambiguous_binding", "negation",
    "unsupported_question", "too_many_requirements",
]


class QuestionSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: Annotated[StrictInt, Field(ge=0)]
    end: Annotated[StrictInt, Field(gt=0)]
    text: SpanText


class ScopeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_model: Label
    parameter: Label
    product_span: QuestionSpan
    parameter_span: QuestionSpan

    @field_validator("product_model", "parameter")
    @classmethod
    def nonblank_label(cls, value):
        if not value.strip() or value != value.strip():
            raise ValueError("标签必须是不含首尾空白的原文")
        return value


class ScopeIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: IssueCode
    span: QuestionSpan


class ScopeProposalPayload(BaseModel):
    """The complete, closed output vocabulary accepted from a provider."""
    model_config = ConfigDict(extra="forbid")
    candidates: Annotated[list[ScopeCandidate], Field(max_length=10)]
    issues: Annotated[list[ScopeIssue], Field(max_length=10)]
