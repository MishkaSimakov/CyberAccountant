"""Locate report rows by their exact configured label."""

import re

from checkier.entities import ENT_RE, EntityRegistry

_TAGMARKUP_RE = re.compile(r"</?ent\b[^>]*>")


def _target_label_spans(text: str, target_label: str) -> list[tuple[int, int]]:
    """Locate the label in text with entity markup removed."""
    pattern = re.compile(rf"(?<!\w){re.escape(target_label)}(?!\w)")
    return [match.span() for match in pattern.finditer(_TAGMARKUP_RE.sub("", text))]


def find_target_row_spans(text: str, target_label: str) -> list[tuple[int, int]]:
    """Find matching line spans in OCR Markdown, without needing entities."""
    spans = []
    offset = 0
    for line in text.splitlines(keepends=True):
        end = offset + len(line)
        if _target_label_spans(line, target_label):
            spans.append((offset, end))
        offset = end
    return spans


def _target_row_entity_spans(text: str, target_label: str) -> set[tuple[int, int]]:
    spans = set()
    for start, end in find_target_row_spans(text, target_label):
        label_spans = _target_label_spans(text[start:end], target_label)
        for match in ENT_RE.finditer(text, start, end):
            entity_start, entity_end = (
                len(_TAGMARKUP_RE.sub("", text[start:offset])) for offset in match.span()
            )
            if not any(entity_start < label_end and label_start < entity_end
                       for label_start, label_end in label_spans):
                spans.add(match.span())
    return spans


def filter_target_row_entities(text: str, target_label: str | None) -> str:
    """Unwrap non-target entities, preserving document text and target markup."""
    if not target_label:
        return text
    spans = _target_row_entity_spans(text, target_label)
    return ENT_RE.sub(lambda match: match.group(0) if match.span() in spans else match.group(2), text)


def find_target_row_keys(
        tagged: dict[str, tuple[str, str]],
        registry: EntityRegistry,
        target_label: str | None,
) -> set[int] | None:
    """Return selected IDs; None disables targeting, while an empty set selects none."""
    if not target_label:
        return None
    spans = {file_id: _target_row_entity_spans(text, target_label)
             for file_id, (role, text) in tagged.items() if role == "card"}
    return {entity.num for entity in registry.entities
            if entity.role == "card" and entity.span in spans.get(entity.file, set())}
