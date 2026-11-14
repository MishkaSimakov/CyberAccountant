"""Canonical reviewed inputs shared by requirements and verification."""

from dataclasses import dataclass

from .documents import DocumentRevision, ReviewedDocument
from .errors import ProcessingValidationError


@dataclass(frozen=True)
class ReviewedCorpus:
    """One report's canonical Markdown snapshots, without a mutable entity cache."""

    report_id: int
    documents: tuple[ReviewedDocument, ...]

    def __post_init__(self) -> None:
        if self.report_id <= 0:
            raise ProcessingValidationError("Report IDs must be positive.")
        seen = set()
        for document in self.documents:
            revision = document.revision
            if revision.report_id != self.report_id:
                raise ProcessingValidationError("All corpus documents must belong to the same report.")
            if revision.document_id in seen:
                raise ProcessingValidationError("A corpus must contain one revision per document.")
            seen.add(revision.document_id)

    @property
    def revisions(self) -> tuple[DocumentRevision, ...]:
        return tuple(document.revision for document in self.documents)
