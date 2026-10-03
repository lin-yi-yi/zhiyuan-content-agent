"""Explicit facts requested by a task, independent of query wording."""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class RequiredFact(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    product_model: str = Field(min_length=1, max_length=200)
    parameter: str = Field(min_length=1, max_length=200)


RequiredFacts = Annotated[list[RequiredFact], Field(max_length=10)]
required_facts_adapter = TypeAdapter(RequiredFacts)
