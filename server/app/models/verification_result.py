"""One published verification result file per report."""

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class VerificationResult(Base):
    __tablename__ = "verification_results"

    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True)
    path: Mapped[str]
