"""Report scope passed to processing without an application Report object."""

from dataclasses import dataclass
from datetime import date

from .errors import ProcessingValidationError


@dataclass(frozen=True)
class Scope:
    contract_number: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    target_label: str | None = None
    # Numeric IDs are local to the input corpus's report. None means all fields;
    # an empty tuple means no fields were selected.
    target_row: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if (self.period_start is None) != (self.period_end is None):
            raise ProcessingValidationError("A reporting period needs both start and end dates.")
        if self.period_start is not None and self.period_start > self.period_end:
            raise ProcessingValidationError("The reporting period must start on or before its end.")
