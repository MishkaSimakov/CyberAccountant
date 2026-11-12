"""Validate entity markup and allocate IDs when a document revision is saved."""

import re
from html import escape
from html.parser import HTMLParser

from fastapi import HTTPException

from .schemas.entities import EntityAttributes

ENTITY = re.compile(r'<entity\b((?:"[^"]*"|\'[^\']*\'|[^\'">])*)>(.*?)</entity\s*>', re.I | re.S)
next_entity_id = 1


class AttributeParser(HTMLParser):
    def handle_starttag(self, tag, attrs):
        self.attributes = dict(attrs)
        if len(self.attributes) != len(attrs):
            raise ValueError("Атрибуты сущности не должны повторяться.")


def attributes_for(match: re.Match) -> dict[str, str]:
    parser = AttributeParser()
    parser.feed(f"<entity{match[1]}>")
    return parser.attributes


def assign_entity_ids(content: str) -> str:
    """Assign fresh IDs to server-generated markup, including OCR results."""
    return ENTITY.sub(lambda match: entity_markup(attributes_for(match), match[2], allocate_id()), content)


def allocate_id() -> str:
    global next_entity_id
    entity_id = next_entity_id
    next_entity_id += 1
    return str(entity_id)


def entity_markup(attributes: dict[str, str], text: str, entity_id: str) -> str:
    fields = " ".join(f'{name}="{escape(attributes[name], quote=True)}"' for name in ("type", "value", "unit"))
    return f'<entity {fields} id="{entity_id}">{text}</entity>'


def prepare_entities(content: str, previous: str) -> str:
    known_ids = {attributes_for(match).get("id") for match in ENTITY.finditer(previous)}
    seen = set()

    def replace(match: re.Match) -> str:
        attributes = attributes_for(match)
        entity_id = attributes.pop("id", None)
        EntityAttributes.model_validate(attributes)
        if "<entity" in match[2].lower():
            raise ValueError("Сущности не должны пересекаться.")
        if entity_id is not None:
            if entity_id not in known_ids or entity_id in seen:
                raise ValueError("ID сущности задаётся сервером и не может быть изменён.")
            seen.add(entity_id)
        return entity_markup(attributes, match[2], entity_id or allocate_id())

    try:
        if re.search(r"</?entity\b", ENTITY.sub("", content), re.I):
            raise ValueError("Некорректная разметка сущностей.")
        return ENTITY.sub(replace, content)
    except ValueError as error:
        raise HTTPException(422, "Проверьте тип, значение, единицу и разметку сущностей. ID задаётся сервером.") from error
