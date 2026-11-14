"""Parse entity wrappers without rewriting their attributes or source text."""

import re
from dataclasses import dataclass
from datetime import date
from html import unescape

from processing.common.errors import ProcessingValidationError

ENT_RE = re.compile(r'<ent\s+((?:[^>"\']|"[^"]*"|\'[^\']*\')*)>(.*?)</ent>', re.DOTALL)
ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
_ENTITY_MARKER = re.compile(r"</?(?:ent|entity)\b", re.IGNORECASE)


@dataclass(frozen=True)
class EntityTag:
    """Offsets refer to the supplied Markdown, before identity insertion."""

    attributes: dict[str, str]
    span: tuple[int, int]
    body_span: tuple[int, int]


def parse_tag_attrs(raw: str) -> dict[str, str]:
    """Read double-quoted attributes, rejecting duplicates and broken syntax."""
    attributes: dict[str, str] = {}
    end = 0
    for match in ATTR_RE.finditer(raw):
        gap = raw[end:match.start()]
        if gap.strip() or (end and not gap):
            raise ProcessingValidationError("Malformed entity attributes.")
        name, value = match.groups()
        if name in attributes:
            raise ProcessingValidationError(f"Malformed or duplicate entity attributes: {name}.")
        attributes[name] = unescape(value)
        end = match.end()
    if raw[end:].strip():
        raise ProcessingValidationError("Malformed entity attributes.")
    return attributes


def parse_entity_tags(markdown: str) -> list[EntityTag]:
    """Read wrappers in source order; reject incomplete or nested entities."""
    tags: list[EntityTag] = []
    end = 0
    for match in ENT_RE.finditer(markdown):
        if _ENTITY_MARKER.search(markdown[end:match.start()]) or _ENTITY_MARKER.search(match.group(2)):
            raise ProcessingValidationError("Malformed or nested entity markup.")
        tags.append(EntityTag(parse_tag_attrs(match.group(1)), match.span(), match.span(2)))
        end = match.end()
    if _ENTITY_MARKER.search(markdown[end:]):
        raise ProcessingValidationError("Malformed entity markup.")
    return tags


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
