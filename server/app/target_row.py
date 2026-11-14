"""Locate report rows by their exact configured label."""

import re

from checkier.entities import EntityRegistry

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


def find_target_row_keys(
        tagged: dict[str, tuple[str, str]],
        registry: EntityRegistry,
        target_label: str | None,
) -> set[int] | None:
    """Return IDs on matching card lines, excluding entities in the row label."""
    if not target_label:
        return None

    keys = set()
    for file_id, (role, text) in tagged.items():
        if role != "card":
            continue
        for start, end in find_target_row_spans(text, target_label):
            line = text[start:end]
            label_spans = _target_label_spans(line, target_label)
            for entity in registry.entities:
                if not (entity.file == file_id and entity.role == "card"
                        and start <= entity.span[0] and entity.span[1] <= end):
                    continue
                entity_start, entity_end = (
                    len(_TAGMARKUP_RE.sub("", text[start:offset])) for offset in entity.span
                )
                if not any(entity_start < label_end and label_start < entity_end
                           for label_start, label_end in label_spans):
                    keys.add(entity.num)
    return keys or None
