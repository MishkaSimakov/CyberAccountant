"""Validate review markup, assign identities, and maintain derived file lookups."""

from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.orm import Session

from processing.entities.identifiers import assign_identities, read_identities
from processing.entities.markup import parse_entity_tags

from .entity_ids import reserve_entity_ids
from .models import EntityFile
from .schemas.entities import EntityAttributes


def prepare_entities(content: str) -> str:
    """Validate tags without allocating registry IDs or altering source text."""

    try:
        read_identities(content)
        for tag in parse_entity_tags(content):
            EntityAttributes.model_validate(tag.attributes)
        return content
    except ValueError as error:
        raise HTTPException(422, "Проверьте разметку <ent>, атрибуты и идентификаторы сущностей.") from error


def assign_document_identities(session: Session, report_id: int, file_id: int,
                               content: str, previous: str) -> str:
    """Assign drafts and replace the derived index under the caller's report lock.

    Existing identities must belong to this document and retain their source
    text. Passing an empty previous document requires extraction-only drafts.
    Removed IDs stay retired because reservations only advance the counter.
    """
    content = prepare_entities(content)
    read_identities(previous)
    tags = parse_entity_tags(content)
    supplied = read_identities(content)
    existing = {
        int(tag.attributes["id"]): (tag.attributes["mnemonic"], previous[slice(*tag.body_span)])
        for tag in parse_entity_tags(previous) if "id" in tag.attributes
    }
    for tag in tags:
        if "id" in tag.attributes and existing.get(int(tag.attributes["id"])) != (
                tag.attributes["mnemonic"], content[slice(*tag.body_span)]):
            raise HTTPException(422, "Нельзя назначать или переносить существующие идентификаторы сущностей.")
    count = len(tags) - len(supplied)
    try:
        content = assign_identities(content, reserve_entity_ids(session, report_id, count))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    session.execute(delete(EntityFile).where(EntityFile.report_id == report_id, EntityFile.file_id == file_id))
    session.add_all(EntityFile(report_id=report_id, entity_id=identity.num, file_id=file_id)
                    for identity in read_identities(content))
    return content
