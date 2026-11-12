"""Rebuildable lookup from entities to the files containing their XML tags."""

from pydantic import BaseModel, Field


class EntityFile(BaseModel):
    """(report_id, entity_id) identifies an entity; file_id is the logical file ID.

    Entity attributes and text remain in the XML at the file's entities_path.
    """

    report_id: int = Field(ge=1)
    entity_id: int = Field(ge=1)
    file_id: int = Field(ge=1)
