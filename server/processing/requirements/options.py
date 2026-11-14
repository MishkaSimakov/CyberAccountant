"""Storage-independent settings for requirement generation."""

from dataclasses import dataclass
from typing import Literal

from processing.common.errors import ProcessingValidationError

DEFAULT_REL_MODEL = "deepseek/deepseek-v4.1-flash-1017"


@dataclass(frozen=True)
class RequirementOptions:
    rel_model: str = DEFAULT_REL_MODEL
    id_style: Literal["numeric", "mnemonic"] = "numeric"
    max_retries: int = 2
    request_timeout: float = 300.0
    reasoning: str = "disabled"

    def __post_init__(self) -> None:
        if (not self.rel_model.strip() or self.id_style not in {"numeric", "mnemonic"}
                or self.max_retries < 0 or self.request_timeout <= 0):
            raise ProcessingValidationError("Invalid requirement generation options.")

    @classmethod
    def from_settings(cls, settings) -> "RequirementOptions":
        return cls(settings.rel_model, settings.id_style, settings.max_retries,
                   settings.request_timeout, settings.reasoning)
