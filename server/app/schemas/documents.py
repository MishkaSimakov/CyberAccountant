"""Document and markdown request and response schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class Document(BaseModel):
    id: int
    name: str
    size: int
    content_type: str
    role: Literal["final_report", "supporting"]
    content_sha256: str = Field(exclude=True)
    status: Literal[
        "ocr_queued", "ocr_running", "ocr_completed", "entities_queued", "entities_running", "completed", "failed"]
    markdown_revision: int = 0
    user_edited: bool = False
    error: str | None = None


class MarkdownRevision(BaseModel):
    content: str = Field(max_length=1_000_000)
    revision: int


class MarkdownEdit(BaseModel):
    content: str = Field(min_length=1, max_length=1_000_000)
    base_revision: int = Field(ge=1)
