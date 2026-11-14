"""Storage-independent settings for extraction and normalization."""

from dataclasses import dataclass

from processing.common.errors import ProcessingValidationError

DEFAULT_TAG_MODEL = "deepseek/deepseek-v4.1-flash-1017"


@dataclass(frozen=True)
class EntityOptions:
    tag_model: str = DEFAULT_TAG_MODEL
    max_retries: int = 2
    request_timeout: float = 300.0
    reasoning: str = "disabled"

    def __post_init__(self) -> None:
        if not self.tag_model.strip() or self.max_retries < 0 or self.request_timeout <= 0:
            raise ProcessingValidationError("Entity processing needs a model, nonnegative retries and positive timeout.")

    @classmethod
    def from_settings(cls, settings) -> "EntityOptions":
        """Copy stage settings when a standalone caller owns broader configuration."""
        return cls(settings.tag_model, settings.max_retries, settings.request_timeout, settings.reasoning)
