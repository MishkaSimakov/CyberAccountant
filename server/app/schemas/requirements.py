"""Requirement request and response schemas."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class RequirementMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    text: str | None = None
    source: str = ""
    notes: str = ""
    silenced: bool = False


class FormulaRequirement(RequirementMetadata):
    kind: Literal["formula"]
    equation: str
    tag: Literal["contract", "period", "agg", "ref"] | None = None


class AssertionRequirement(RequirementMetadata):
    kind: Literal["assertion"]
    condition: bool | None
    ids_related: list[int | str]


RequirementResponse = Annotated[FormulaRequirement | AssertionRequirement, Field(discriminator="kind")]
requirement_adapter = TypeAdapter(RequirementResponse)


class RequirementUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str = Field(default="", max_length=10000)
    silenced: bool = False


class RequirementsResponse(BaseModel):
    requirements: list[RequirementResponse]
