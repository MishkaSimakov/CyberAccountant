"""Report request and response schemas."""

import re
from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReportCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    contract_number: str | None = Field(default=None, max_length=100)
    period_start: date | None = None
    period_end: date | None = None
    target_row: str | None = Field(default=None, max_length=200,
                                  description="Подпись строки либо её номер с 1 среди строк данных таблицы")

    @field_validator("target_row", mode="before", json_schema_input_type=str | int | None)
    @classmethod
    def normalize_target_row(cls, value):
        if isinstance(value, bool):
            raise ValueError("Номер строки должен быть положительным целым числом.")
        if isinstance(value, int):
            value = str(value)
        if isinstance(value, str) and re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?", value.strip()):
            if not value.strip().isdecimal() or int(value) < 1:
                raise ValueError("Номер строки должен быть положительным целым числом.")
            return str(int(value))
        return value

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if (self.period_start is None) != (self.period_end is None):
            raise ValueError("Укажите начало и конец периода подтверждающих документов.")
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("Конец периода не может быть раньше начала.")
        return self


class Report(ReportCreate):
    id: int
    stage: Literal["documents", "entities", "verification"] = "documents"
    processing_status: Literal["idle", "queued", "running", "completed", "failed"] = "idle"
