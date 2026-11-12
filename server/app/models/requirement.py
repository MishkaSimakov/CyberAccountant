"""Requirement storage while the definition JSON schema is being finalized."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Requirement(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: int = Field(ge=1)
    report_id: int = Field(ge=1)
    json_data: dict[str, JsonValue] = Field(alias="json")
    note: str = ""
    is_silenced: bool = False
