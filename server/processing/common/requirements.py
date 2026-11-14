"""Generated requirement definitions, independent of persistence and evaluation."""

from dataclasses import dataclass, field
from typing import Literal

RequirementTag = Literal["scope", "agg", "ref"]
EntityReference = int | str


@dataclass(frozen=True, kw_only=True)
class FormulaDefinition:
    equation: str
    source: str = ""
    text: str | None = None
    tag: RequirementTag | None = None
    kind: Literal["formula"] = field(default="formula", init=False)


@dataclass(frozen=True, kw_only=True)
class AssertionDefinition:
    """None is an explicitly unresolved assertion; False is a failed assertion."""

    condition: bool | None
    ids_related: tuple[EntityReference, ...] = ()
    source: str = ""
    text: str | None = None
    kind: Literal["assertion"] = field(default="assertion", init=False)


RequirementDefinition = FormulaDefinition | AssertionDefinition
