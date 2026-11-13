"""Entity drafts and parsed views of identities stored in reviewed Markdown."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal

from .documents import DocumentRevision
from .errors import ProcessingValidationError

EntityType = Literal["amount", "date", "docid", "contract", "number", "pct", "ratio"]
EntityLocation = Literal["body", "filename"]


class UnknownValue(Enum):
    """An unresolved normalized value, distinct from zero, false or empty text."""

    UNKNOWN = "unknown"


NormalizedValue = Decimal | date | str | UnknownValue


@dataclass(frozen=True)
class EntityIdentity:
    """Numeric ID and mnemonic read from a tag, unique within one report.

    The same numeric ID may occur in another report. Creating this value never
    allocates an ID or derives one from an entity's position.
    """

    num: int
    mnemonic: str

    def __post_init__(self) -> None:
        if self.num < 0 or not self.mnemonic.strip():
            raise ProcessingValidationError("Entity identities need a nonnegative ID and a mnemonic.")


@dataclass(frozen=True, kw_only=True)
class EntityDraft:
    """Extracted values and source information before identity assignment.

    normalized_value is already in base units; norm records the source
    multiplier and must not be applied to a reviewed value a second time.
    span uses character offsets in the tagged Markdown, including the tag.
    """

    type: EntityType
    raw: str
    normalized_value: NormalizedValue = UnknownValue.UNKNOWN
    unit: str = ""
    norm: Decimal = Decimal("1")
    span: tuple[int, int] = (0, 0)
    loc: EntityLocation = "body"
    source_text: str = ""
    normalized_from: str = ""


@dataclass(frozen=True, kw_only=True)
class Entity(EntityDraft):
    """A derived entity view tied to its canonical document revision."""

    identity: EntityIdentity
    revision: DocumentRevision
