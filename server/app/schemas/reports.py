"""Report request and response schemas."""

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReportCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    contract_number: str | None = Field(default=None, max_length=100)
    period_start: date | None = None
    period_end: date | None = None
    target_row: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if (self.period_start is None) != (self.period_end is None):
            raise ValueError("Укажите начало и конец периода подтверждающих документов.")
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("Конец периода не может быть раньше начала.")
        return self


class Report(ReportCreate):
    id: int
    stage: Literal["documents", "entities", "requirements", "verification"] = "documents"
    processing_status: Literal["idle", "queued", "running", "completed", "failed"] = "idle"
