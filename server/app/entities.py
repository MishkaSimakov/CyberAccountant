"""Validate saved review markup using Checkier's tag syntax."""

from html import escape

from fastapi import HTTPException

from processing.entities.markup import parse_entity_tags

from .schemas.entities import EntityAttributes


def prepare_entities(content: str) -> str:
    """Validate tags without allocating registry IDs or altering source text."""

    try:
        pieces: list[str] = []
        end = 0
        for tag in parse_entity_tags(content):
            EntityAttributes.model_validate(tag.attributes)
            fields = " ".join(f'{name}="{escape(value, quote=True)}"' for name, value in tag.attributes.items())
            pieces.extend((content[end:tag.span[0]], f"<ent {fields}>{content[slice(*tag.body_span)]}</ent>"))
            end = tag.span[1]
        pieces.append(content[end:])
        return "".join(pieces)
    except ValueError as error:
        raise HTTPException(422, "Проверьте разметку <ent> и атрибуты t, u, n, value.") from error
