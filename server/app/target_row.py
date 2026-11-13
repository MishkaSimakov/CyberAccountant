"""Locate report rows by their exact configured label."""

import re

from checkier.entities import EntityRegistry

_TAGMARKUP_RE = re.compile(r"</?ent\b[^>]*>")


def find_target_row_keys(
        tagged: dict[str, tuple[str, str]],
        registry: EntityRegistry,
        target_label: str | None,
) -> set[int] | None:
    """Return IDs on matching card lines, or None if no target entities exist."""
    if not target_label:
        return None

    pattern = re.compile(rf"(?<!\w){re.escape(target_label)}(?!\w)")
    keys = set()
    for file_id, (role, text) in tagged.items():
        if role != "card":
            continue
        offset = 0
        for line in text.splitlines(keepends=True):
            end = offset + len(line)
            if pattern.search(_TAGMARKUP_RE.sub("", line)):
                keys.update(entity.num for entity in registry.entities
                            if entity.file == file_id and entity.role == "card"
                            and offset <= entity.span[0] and entity.span[1] <= end)
            offset = end
    return keys or None
