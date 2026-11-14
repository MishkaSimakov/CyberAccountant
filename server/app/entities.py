"""Validate saved review markup using Checkier's tag syntax."""

import re
from html import escape

from fastapi import HTTPException

from checkier.entities import ATTR_RE, ENT_RE, parse_tag_attrs

from .schemas.entities import EntityAttributes


def prepare_entities(content: str) -> str:
    """Validate tags without allocating registry IDs or altering source text."""

    def replace(match: re.Match) -> str:
        raw_attributes = match.group(1)
        attributes = parse_tag_attrs(raw_attributes)
        if ATTR_RE.sub("", raw_attributes).strip() or len(attributes) != len(ATTR_RE.findall(raw_attributes)):
            raise ValueError("Некорректные или повторяющиеся атрибуты сущности.")
        EntityAttributes.model_validate(attributes)
        if re.search(r"</?(?:ent|entity)\b", match.group(2), re.I):
            raise ValueError("Сущности не должны пересекаться.")
        fields = " ".join(f'{name}="{escape(value, quote=True)}"' for name, value in attributes.items())
        return f"<ent {fields}>{match.group(2)}</ent>"

    try:
        if re.search(r"</?(?:ent|entity)\b", ENT_RE.sub("", content), re.I):
            raise ValueError("Некорректная разметка сущностей.")
        return ENT_RE.sub(replace, content)
    except ValueError as error:
        raise HTTPException(422, "Проверьте разметку <ent> и атрибуты t, u, n, value, start, end.") from error
