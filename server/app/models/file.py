"""File revisions and paths to their preserved artifacts."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class File(BaseModel):
    """One revision; file_id stays stable and (file_id, version) is unique.

    Unchanged artifacts may share paths between revisions. hand_edited describes
    this revision; any hand-edited revision marks the logical file as user-edited.
    """

    id: int = Field(ge=1)
    file_id: int = Field(ge=1)
    report_id: int = Field(ge=1)
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    hash: str = Field(min_length=1)
    markdown_path: str | None = None
    entities_path: str | None = None
    role: Literal["final_report", "supporting"]
    status: Literal[
        "queued", "ocr_running", "ocr_completed", "entities_running", "completed", "failed"
    ] = "queued"
    error: str | None = None
    size: int = Field(ge=0)
    content_type: str = Field(min_length=1)
    hand_edited: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
