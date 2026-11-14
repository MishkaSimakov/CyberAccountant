"""Data-only contracts for replaceable requirement generation."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import cached_property

from processing.common.corpus import ReviewedCorpus
from processing.common.llm import LoggerLike
from processing.common.requirements import RequirementDefinition
from processing.common.scope import Scope
from processing.common.registry import EntityRegistry
from processing.entities.corpus import build_registry


@dataclass(frozen=True)
class RequirementInput:
    corpus: ReviewedCorpus
    scope: Scope

    @cached_property
    def registry(self) -> EntityRegistry:
        return build_registry({str(doc.revision.document_id): (
            "card" if doc.role == "final_report" else "data", doc.markdown,
        ) for doc in self.corpus.documents}, revisions={
            str(doc.revision.document_id): doc.revision for doc in self.corpus.documents})


@dataclass(frozen=True)
class RequirementOutput:
    definitions: tuple[RequirementDefinition, ...]


class RequirementGenerator(ABC):
    @abstractmethod
    def generate(self, source: RequirementInput, *, logger: LoggerLike) -> list[RequirementDefinition]:
        """Return definitions without evaluation, identity allocation or persistence."""


class RequirementProcessor(ABC):
    @abstractmethod
    def process(self, source: RequirementInput, *, logger: LoggerLike) -> RequirementOutput:
        """Generate definitions from one immutable reviewed snapshot."""
