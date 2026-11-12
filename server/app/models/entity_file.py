"""Rebuildable lookup from entities to the files containing their XML tags."""

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class EntityFile(Base):
    """(report_id, entity_id) identifies an entity; file_id is the logical file ID.

    Entity attributes and text remain in the XML at the file's entities_path.
    file_id is shared by multiple revisions, so it cannot reference files.id.
    """

    __tablename__ = "entity_files"
    __table_args__ = (
        CheckConstraint("entity_id >= 0", name="entity_file_nonnegative_entity_id"),
        CheckConstraint("file_id > 0", name="entity_file_positive_file_id"),
        Index("ix_entity_files_report_id_file_id", "report_id", "file_id"),
    )

    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True)
    entity_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    file_id: Mapped[int]
