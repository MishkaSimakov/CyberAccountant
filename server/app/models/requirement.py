"""Requirements and user annotations; the evolving definition is stored as JSONB."""

from typing import Any

from sqlalchemy import ForeignKey, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class Requirement(Base):
    """A requirement ID is local to its report and allocated by the writer."""

    __tablename__ = "requirements"

    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True)
    requirement_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    json_data: Mapped[dict[str, Any]] = mapped_column("json", MutableDict.as_mutable(JSONB))
    text: Mapped[str | None]
    source: Mapped[str] = mapped_column(server_default=text("''"))
    note: Mapped[str] = mapped_column(server_default=text("''"))
    is_silenced: Mapped[bool] = mapped_column(server_default=text("false"))
