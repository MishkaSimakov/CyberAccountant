"""Data-only, replaceable verification stage contracts."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import cached_property

from processing.common.corpus import ReviewedCorpus
from processing.common.llm import LoggerLike
from processing.common.registry import EntityRegistry
from processing.common.scope import Scope
from processing.entities.corpus import build_registry
from .evaluation import EvaluationResult, SavedRequirement


@dataclass(frozen=True)
class VerificationInput:
    corpus: ReviewedCorpus
    scope: Scope
    requirements: tuple[SavedRequirement, ...]

    @cached_property
    def registry(self) -> EntityRegistry:
        return build_registry({str(doc.revision.document_id): (
            "card" if doc.role == "final_report" else "data", doc.markdown,
        ) for doc in self.corpus.documents}, revisions={
            str(doc.revision.document_id): doc.revision for doc in self.corpus.documents})


@dataclass(frozen=True)
class VerificationOutput:
    requirement_results: dict[int, EvaluationResult]
    expected_values: dict[int, str]
    uniqueness: dict[int, str]
    findings: dict
    diagnostics: tuple[dict, ...]
    report: dict


class VerificationProcessor(ABC):
    @abstractmethod
    def process(self, source: VerificationInput, *, logger: LoggerLike) -> VerificationOutput:
        """Evaluate a reviewed snapshot without persistence, inference clients or ORM objects."""
