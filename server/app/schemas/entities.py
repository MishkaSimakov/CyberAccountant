"""Validation schema for entity attributes stored in XML tags."""

import re
from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EntityAttributes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["id", "date", "number"]
    value: str = Field(min_length=1, max_length=1_000_000)
    unit: Literal["string", "date", "rubles", "euros", "kg"]

    @model_validator(mode="after")
    def validate_attributes(self) -> Self:
        if not self.value.strip():
            raise ValueError("Укажите значение сущности.")
        if self.type == "id" and self.unit != "string":
            raise ValueError("Для идентификатора используйте unit=string.")
        if self.type == "date":
            if self.unit != "date" or not re.fullmatch(r"\d{4}(?:-\d{2}){0,2}", self.value):
                raise ValueError("Укажите дату в формате YYYY, YYYY-MM или YYYY-MM-DD и unit=date.")
            date.fromisoformat(self.value + {4: "-01-01", 7: "-01", 10: ""}[len(self.value)])
        if self.type == "number":
            if self.unit not in {"rubles", "euros", "kg"} or not re.fullmatch(r"-?\d+(?:\.\d+)?", self.value):
                raise ValueError("Укажите число с точкой и единицу rubles, euros или kg.")
        return self
