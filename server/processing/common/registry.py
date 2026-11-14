"""Derived entity index records used by requirement and solver adapters."""

from dataclasses import dataclass
from typing import Iterable

from .documents import DocumentRevision

@dataclass
class RegistryEntity:
    num: int
    mnemonic: str
    type: str  # amount | date | docid | contract | number | pct | ratio
    raw: str
    value: str | int | float | None  # normalized: Decimal->str, date->ordinal, else raw
    unit: str
    norm: str  # multiplier as string, "1" default
    file: str  # obfuscated filename (h8)
    role: str  # data | card | request
    span: tuple[int, int]  # (start, end) in the tagged doc
    loc: str = "body"  # body | filename
    source_text: str = ""  # the raw fragment inside the tag
    revision: DocumentRevision | None = None
    normalized_from: str = ""  # set when an LLM normalized an unparsable date raw

    def to_dict(self) -> dict:
        return {
            "num": self.num,
            "mnemonic": self.mnemonic,
            "type": self.type,
            "raw": self.raw,
            "value": self.value,
            "unit": self.unit,
            "norm": self.norm,
            "file": self.file,
            "role": self.role,
            "span": list(self.span),
            "loc": self.loc,
            "source_text": self.source_text,
            "normalized_from": self.normalized_from,
        }

    @property
    def key(self) -> str:
        """Identifier used in equations, per id_style set on the registry."""
        return self.mnemonic


class EntityRegistry:
    """An in-memory index; identities and values are read from canonical inputs."""

    def __init__(self, entities: Iterable[RegistryEntity] = ()) -> None:
        self.entities = list(entities)

    def to_dict(self) -> dict:
        return {"entities": [entity.to_dict() for entity in self.entities]}
