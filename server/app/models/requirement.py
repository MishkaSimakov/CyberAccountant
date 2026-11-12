"""Requirements and user annotations; the evolving definition is stored as JSONB."""

from enum import StrEnum
from typing import Any

from sqlalchemy import Enum, ForeignKey, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class RequirementVerdict(StrEnum):
    TRUE = "true"
    FALSE = "false"
    ERROR = "error"
    UNVERIFIABLE = "unverifiable"


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    verdict: Mapped[RequirementVerdict] = mapped_column(Enum(
        RequirementVerdict, name="requirement_verdict",
        values_callable=lambda enum: [value.value for value in enum],
        validate_strings=True,
    ))
    json_data: Mapped[dict[str, Any]] = mapped_column("json", MutableDict.as_mutable(JSONB))
    note: Mapped[str] = mapped_column(server_default=text("''"))
    is_silenced: Mapped[bool] = mapped_column(server_default=text("false"))
