"""Report-local identities assigned from caller-reserved numeric IDs.

New mnemonics encode IDs in base 2048: both words use the full vendored list.
IDs are zero-based and the half-open capacity is [0, 2048**2). Existing
complete identities are retained, including previously supplied word pairs.
No allocation, registry lookup, random sampling, or source rewriting occurs.
"""

import re
from functools import lru_cache
from importlib import resources

from processing.common.entities import EntityIdentity
from processing.common.errors import ProcessingValidationError

from .markup import EntityTag, parse_entity_tags

WORD_COUNT = 2048
MNEMONIC_CAPACITY = WORD_COUNT ** 2


@lru_cache(maxsize=1)
def mnemonic_words() -> tuple[str, ...]:
    """Read the canonical list; missing or invalid data must fail explicitly."""
    words = tuple(resources.files("processing.entities.data").joinpath("bip39_english.txt").read_text(
        encoding="utf-8").splitlines())
    if len(words) != WORD_COUNT or len(set(words)) != WORD_COUNT or any(
            re.fullmatch(r"[a-z]+", word) is None for word in words):
        raise ProcessingValidationError("Mnemonic wordlist must contain 2048 unique lowercase words.")
    return words


def _validate_id(num: int) -> None:
    if type(num) is not int or not 0 <= num < MNEMONIC_CAPACITY:
        raise ProcessingValidationError(f"Entity ID must be in [0, {MNEMONIC_CAPACITY}).")


def mnemonic_for_id(num: int) -> str:
    """Encode one report-local ID as a formula-compatible two-word mnemonic."""
    _validate_id(num)
    words = mnemonic_words()
    first, second = divmod(num, WORD_COUNT)
    return f"{words[first]}-{words[second]}"


def id_for_mnemonic(mnemonic: str) -> int:
    """Decode the bijective mapping, rejecting unknown or malformed words."""
    parts = mnemonic.split("-")
    words = mnemonic_words()
    if len(parts) != 2 or any(part not in words for part in parts):
        raise ProcessingValidationError("Mnemonic must contain two canonical words separated by a hyphen.")
    return words.index(parts[0]) * WORD_COUNT + words.index(parts[1])


def _tag_identity(tag: EntityTag) -> EntityIdentity | None:
    attrs = tag.attributes
    if "id" not in attrs and "mnemonic" not in attrs:
        return None
    if "id" not in attrs or "mnemonic" not in attrs or re.fullmatch(r"0|[1-9][0-9]*", attrs["id"]) is None:
        raise ProcessingValidationError("Entity identity requires a numeric id and mnemonic together.")
    # Bound the textual length before int() as well as the numeric value.
    if len(attrs["id"]) > len(str(MNEMONIC_CAPACITY - 1)):
        raise ProcessingValidationError("Entity ID exceeds mnemonic capacity.")
    num = int(attrs["id"])
    _validate_id(num)
    id_for_mnemonic(attrs["mnemonic"])
    return EntityIdentity(num=num, mnemonic=attrs["mnemonic"])


def _identities(tags: list[EntityTag]) -> list[EntityIdentity | None]:
    identities = [_tag_identity(tag) for tag in tags]
    nums: set[int] = set()
    mnemonics: set[str] = set()
    for identity in identities:
        if identity is None:
            continue
        if identity.num in nums or identity.mnemonic in mnemonics:
            raise ProcessingValidationError("Duplicate entity ID or mnemonic.")
        nums.add(identity.num)
        mnemonics.add(identity.mnemonic)
    return identities


def read_identities(markdown: str) -> list[EntityIdentity]:
    """Validate markup and identities, returning complete identities in order.

    Draft tags are allowed, but partial identities and duplicates are errors.
    Applications can combine documents before calling to check report scope.
    """
    return [identity for identity in _identities(parse_entity_tags(markdown)) if identity is not None]


def assign_identities(markdown: str, reserved_ids: range) -> str:
    """Add identities only to drafts, consuming exactly the supplied ID range.

    The caller owns ID reservation. Range length must equal the draft count;
    IDs must be contiguous and within capacity. Collisions with existing IDs
    or mnemonics fail instead of skipping or silently replacing identities.
    """
    if not isinstance(reserved_ids, range) or reserved_ids.step != 1:
        raise ProcessingValidationError("Reserved IDs must be a contiguous range.")
    if not 0 <= reserved_ids.start <= reserved_ids.stop <= MNEMONIC_CAPACITY:
        raise ProcessingValidationError("Reserved IDs exceed mnemonic capacity.")
    tags = parse_entity_tags(markdown)
    identities = _identities(tags)
    if len(reserved_ids) != identities.count(None):
        raise ProcessingValidationError("Reserved ID count must equal the number of draft entities.")
    nums = {identity.num for identity in identities if identity is not None}
    mnemonics = {identity.mnemonic for identity in identities if identity is not None}
    supplied = iter(reserved_ids)
    pieces: list[str] = []
    end = 0
    for tag, identity in zip(tags, identities):
        if identity is not None:
            continue
        num = next(supplied)
        mnemonic = mnemonic_for_id(num)
        if num in nums or mnemonic in mnemonics:
            raise ProcessingValidationError("Reserved identity collides with an existing ID or mnemonic.")
        # Insert immediately after '<ent'; every existing byte stays intact.
        insertion = tag.span[0] + len("<ent")
        pieces.extend((markdown[end:insertion], f' id="{num}" mnemonic="{mnemonic}"'))
        end = insertion
    pieces.append(markdown[end:])
    return "".join(pieces)
