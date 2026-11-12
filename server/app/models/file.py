"""File revisions and paths to their preserved artifacts."""

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Sequence, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from .base import Timestamps


class File(Timestamps, Base):
    """One revision; file_id stays stable and (file_id, version) is unique.

    Unchanged artifacts may share paths between revisions. hand_edited describes
    this revision; any hand-edited revision marks the logical file as user-edited.
    """

    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("file_id", "version", name="files_file_id_version_key"),
        Index("ix_files_report_id_file_id", "report_id", "file_id"),
        CheckConstraint("file_id > 0", name="file_positive_file_id"),
        CheckConstraint("version > 0", name="file_positive_version"),
        CheckConstraint("size >= 0", name="file_nonnegative_size"),
        CheckConstraint("role IN ('final_report', 'supporting')", name="file_valid_role"),
        CheckConstraint(
            "status IN ('queued', 'ocr_running', 'ocr_completed', 'entities_running', 'completed', 'failed')",
            name="file_valid_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(Sequence("logical_file_id_seq", metadata=Base.metadata))
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(server_default=text("1"))
    name: Mapped[str]
    path: Mapped[str]
    hash: Mapped[str] = mapped_column(String(64))
    markdown_path: Mapped[str | None]
    entities_path: Mapped[str | None]
    role: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), server_default=text("'queued'"))
    error: Mapped[str | None]
    size: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str]
    hand_edited: Mapped[bool] = mapped_column(server_default=text("false"))
