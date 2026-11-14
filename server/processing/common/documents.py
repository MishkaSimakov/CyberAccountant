"""Document snapshots without persistence objects or storage paths."""

from dataclasses import dataclass
from typing import Literal

from .errors import ProcessingValidationError

DocumentRole = Literal["final_report", "supporting"]


@dataclass(frozen=True)
class DocumentRevision:
    report_id: int
    document_id: int
    version: int

    def __post_init__(self) -> None:
        if self.report_id <= 0 or self.document_id <= 0 or self.version <= 0:
            raise ProcessingValidationError("Document and report IDs and revision versions must be positive.")


@dataclass(frozen=True)
class ReviewedDocument:
    """Canonical tagged Markdown for exactly one reviewed revision.

    Entity identities and reviewed values live in markdown. Parsed entities are
    derived views, never a second editable representation of this document.
    """

    revision: DocumentRevision
    name: str
    role: DocumentRole
    markdown: str
