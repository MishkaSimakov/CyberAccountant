"""Parse entity wrappers without rewriting their attributes or source text."""

import re
from dataclasses import dataclass
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
