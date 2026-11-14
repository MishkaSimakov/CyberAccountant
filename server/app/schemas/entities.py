"""Validation for Checkier's review markup attributes."""

import re
from datetime import date, datetime, time
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EntityAttributes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Entity type: monetary amount, date, document ID, contract number,
    # plain number, percentage, or ratio. The original text is inside the <ent> tag.
    t: Literal["amount", "date", "docid", "contract", "number", "pct", "ratio"]
    # Source unit, such as RUB, RUB_K (thousand rubles), USD, or person_month.
    # Checkier defaults to RUB for amounts and an empty unit for other types.
    u: str | None = Field(default=None, max_length=1_000_000)
    # Multiplier used when deriving an amount from its original text:
    # 245 with n="1000" becomes 245000 in base units. Defaults to no scaling.
    n: str = "1"
    # Legacy exact-day value for dates; new date markup uses start/end instead.
    # For other entities, an explicit value is used directly without applying n.
    value: str | None = Field(default=None, max_length=1_000_000)
    # Inclusive timezone-free date bounds. Both empty strings mean unresolved.
    start: str | None = Field(default=None, max_length=100)
    end: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_attributes(self) -> Self:
        if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", self.n.strip()):
            raise ValueError("Укажите числовой множитель n.")
        has_interval = self.start is not None or self.end is not None
        if self.t != "date":
            if has_interval:
                raise ValueError("Атрибуты start/end допустимы только для дат.")
            return self
        if self.value and has_interval:
            raise ValueError("Для даты укажите либо value, либо start/end.")
        if self.value:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.value):
                raise ValueError("Укажите нормализованную дату в формате YYYY-MM-DD.")
            date.fromisoformat(self.value)
        if has_interval:
            if self.start is None or self.end is None:
                raise ValueError("Для даты необходимо указать оба атрибута start и end.")
            if not self.start and not self.end:
                return self
            if not self.start or not self.end:
                raise ValueError("Границы даты должны быть обе заполнены или обе пусты.")
            try:
                start = datetime.strptime(self.start, "%Y-%m-%dT%H:%M:%S")
                end = datetime.strptime(self.end, "%Y-%m-%dT%H:%M:%S")
            except ValueError as error:
                raise ValueError("Границы даты должны иметь формат YYYY-MM-DDTHH:MM:SS.") from error
            if start.time() != time(0, 0) or end.time() != time(23, 59, 59):
                raise ValueError("Начало даты должно быть T00:00:00, а конец — T23:59:59.")
            if start > end:
                raise ValueError("Начало интервала даты не может быть позже его конца.")
        return self
