"""Report-local counter for assigning entity identities."""

from sqlalchemy import CheckConstraint, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class ReportEntityCounter(Base):
    __tablename__ = "report_entity_counters"
    __table_args__ = (
        CheckConstraint("next_entity_id >= 0", name="report_entity_counter_nonnegative_next_entity_id"),
    )

    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True)
    next_entity_id: Mapped[int] = mapped_column(server_default=text("0"))
