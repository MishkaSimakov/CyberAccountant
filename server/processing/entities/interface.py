"""Data-only extraction contracts and a replaceable processor boundary."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from processing.common.entities import EntityDraft
from processing.common.llm import LoggerLike, TokenCallback


@dataclass(frozen=True)
class EntityInput:
    filename: str
    markdown: str


@dataclass(frozen=True)
class EntityOutput:
    markdown: str
    drafts: tuple[EntityDraft, ...]
    retries: int = 0


class EntityProcessor(ABC):
    @abstractmethod
    def process(self, source: EntityInput, *, logger: LoggerLike,
                on_token: TokenCallback | None = None) -> EntityOutput:
        """Return normalized draft Markdown; callers own allocation and storage."""
