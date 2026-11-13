"""Requirement request and response schemas."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, TypeAdapter


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
    tag: Literal["scope", "agg", "ref"] | None = None


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


class FormulaDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["formula"]
    equation: str = Field(min_length=1, max_length=10000)
    text: str | None = Field(default=None, max_length=10000)
    tag: Literal["scope", "agg", "ref"] | None = None


class AssertionDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["assertion"]
    text: str = Field(min_length=1, max_length=10000)
    condition: StrictBool | None
    ids_related: list[int | str] = Field(default_factory=list)


RequirementDefinition = Annotated[FormulaDefinition | AssertionDefinition, Field(discriminator="kind")]


class FormulaValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equation: str = Field(max_length=10000)


class FormulaValidationResponse(BaseModel):
    valid: bool
    error: str | None = None


class RequirementsResponse(BaseModel):
    requirements: list[RequirementResponse]
    entity_documents: dict[int, int] = Field(default_factory=dict)
