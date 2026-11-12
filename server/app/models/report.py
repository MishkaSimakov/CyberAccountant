"""Report metadata and processing state."""

from datetime import date

from sqlalchemy import CheckConstraint, String, text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from .base import Timestamps


class Report(Timestamps, Base):
    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="report_name_not_empty"),
        CheckConstraint(
            "(period_start IS NULL AND period_end IS NULL) OR "
            "(period_start IS NOT NULL AND period_end IS NOT NULL AND period_start <= period_end)",
            name="report_valid_period",
        ),
        CheckConstraint("stage IN ('documents', 'entities', 'verification')", name="report_valid_stage"),
        CheckConstraint(
            "processing_status IN ('idle', 'queued', 'running', 'completed', 'failed')",
            name="report_valid_processing_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    contract_number: Mapped[str | None] = mapped_column(String(100))
    period_start: Mapped[date | None]
    period_end: Mapped[date | None]
    target_row: Mapped[str | None] = mapped_column(String(200))
    stage: Mapped[str] = mapped_column(String(32), server_default=text("'documents'"))
    processing_status: Mapped[str] = mapped_column(String(32), server_default=text("'idle'"))
