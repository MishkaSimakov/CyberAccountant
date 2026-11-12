"""Requirement request and response schemas."""

from pydantic import BaseModel, ConfigDict, Field


class RequirementResponse(BaseModel):
    id: int
    verdict: bool
    equation: str | None = None
    evidence: str | None = None
    ids_related: list[int] = []
    notes: str = ""
    silenced: bool = False


class RequirementUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str = Field(default="", max_length=10000)
    silenced: bool = False


class RequirementsResult(BaseModel):
    requirements: list[RequirementResponse]
    entity_documents: dict[int, int]
