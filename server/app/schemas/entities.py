"""Validation for Checkier's review markup attributes."""

import re
from datetime import date
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
    # Reviewed normalized value: e.g. "245000.00", "2026-01-01", or a document ID.
    # An explicit value is used directly, without applying n again. An empty
    # string means unresolved; omission lets Checkier derive it from the tag text.
    value: str | None = Field(default=None, max_length=1_000_000)

    @model_validator(mode="after")
    def validate_attributes(self) -> Self:
        if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", self.n.strip()):
            raise ValueError("Укажите числовой множитель n.")
        if self.t == "date" and self.value:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.value):
                raise ValueError("Укажите нормализованную дату в формате YYYY-MM-DD.")
            date.fromisoformat(self.value)
        return self
