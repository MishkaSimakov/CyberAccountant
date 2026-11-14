"""Validate entity attributes and draft/canonical markup."""

import re
from datetime import date

from processing.common.errors import ProcessingValidationError
from processing.common.markup import EntityTag, parse_entity_tags


def validate_attributes(attributes: dict[str, str]) -> None:
    """Validate entity fields without a framework or persistence dependency."""
    from .schemas import TAG_TYPES

    if set(attributes) - {"id", "mnemonic", "t", "u", "n", "value"}:
        raise ProcessingValidationError("Unknown entity attributes.")
    if attributes.get("t") not in TAG_TYPES:
        raise ProcessingValidationError("An entity must have a valid type t.")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", attributes.get("n", "1").strip()):
        raise ProcessingValidationError("Entity multiplier n must be numeric.")
    if any(len(value) > 1_000_000 for value in attributes.values()):
        raise ProcessingValidationError("Entity attributes are too long.")
    value = attributes.get("value")
    if attributes["t"] == "date" and value:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("Expected ISO date")
            date.fromisoformat(value)
        except ValueError as error:
            raise ProcessingValidationError("Reviewed dates must use a valid YYYY-MM-DD value.") from error


def validate_markup(markdown: str, *, require_identities: bool = False) -> list[EntityTag]:
    """Validate complete/draft tags while preserving every source character."""
    from .identifiers import read_identities

    tags = parse_entity_tags(markdown)
    identities = read_identities(markdown)
    if require_identities and len(identities) != len(tags):
        raise ProcessingValidationError("Canonical entities require both id and mnemonic.")
    for tag in tags:
        validate_attributes(tag.attributes)
    return tags
