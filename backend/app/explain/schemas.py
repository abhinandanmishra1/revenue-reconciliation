"""Output contracts for LLM explanations. Every response is validated here."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Short = Annotated[str, Field(min_length=1, max_length=300)]


class ItemExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: Annotated[str, Field(min_length=1, max_length=600)]
    likely_cause: Annotated[str, Field(min_length=1, max_length=400)]
    recommended_actions: Annotated[list[Short], Field(min_length=1, max_length=4)]
    confidence: Literal["low", "medium", "high"]


class SummaryExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: Annotated[str, Field(min_length=1, max_length=300)]
    top_issues: Annotated[list[Short], Field(min_length=1, max_length=5)]
    recommended_priorities: Annotated[list[Short], Field(min_length=1, max_length=5)]


def json_schema(model: type[BaseModel]) -> dict:
    """Plain JSON Schema for the provider's structured-output setting."""
    schema = model.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    return schema
