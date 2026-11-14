"""Entity registry: parse tagged docs, normalize values, assign global ids."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from checkier.bip39 import MnemonicSampler
from processing.entities.markup import ATTR_RE, ENT_RE, parse_tag_attrs

# ---------------------------------------------------------------------------
# Tag parsing
# ---------------------------------------------------------------------------

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}

# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------

RU_NUM_WORDS = {
    "ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3,
    "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8,
    "девять": 9, "десять": 10,
}


def _parse_ru_amount_words(text: str) -> Decimal | None:
    """Parse Russian spelled-out amounts like 'семьсот сорок четыре тысячи рублей 00 копеек'."""
    words = text.lower().replace(",", " ").split()
    if not any(w in text.lower() for w in ("руб", "копе")):
        return None
    # Try to find digit words we know
    if not any(w in RU_NUM_WORDS or w in ("тысяч", "тысячи", "тысяча") for w in words):
        return None
    total = Decimal(0)
    for w in words:
        if w in RU_NUM_WORDS:
            total += RU_NUM_WORDS[w]
        elif w.startswith("тысяч") or w in ("тысяча", "тысячи"):
            total *= 1000
    # crude fallback for compounds like "семьсот сорок четыре" — not fully handled
    return total if total > 0 else None


def parse_amount(raw: str) -> Decimal | None:
    """Parse a monetary amount string to Decimal. Handles:
    - '245 000,00' / '245 000.00' / '1,110,000.00'
    - '10 862 418,06'
    - Russian words ('семьсот сорок четыре тысячи рублей 00 копеек') — best effort
    Returns None on failure.
    """
    text = raw.strip()
    # Russian spelled-out amounts
    if any(c.isalpha() for c in text) and any(
            w in text.lower() for w in ("руб", "копе")
    ):
        parsed = _parse_ru_amount_words(text)
        if parsed is not None:
            return parsed
    # Remove currency words
    for w in ("руб.", "руб", "коп.", "копеек", "копейки"):
        text = text.replace(w, "")
    text = text.strip()
    if not text:
        return None
    # Determine decimal separator: last comma or dot followed by 1-2 digits
    # Heuristic: if both ',' and '.' present, the rightmost is decimal sep.
    comma = text.rfind(",")
    dot = text.rfind(".")
    if comma != -1 and dot != -1:
        # rightmost is decimal separator
        if comma > dot:
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif comma != -1:
        text = text.replace(".", "")
        # comma is decimal separator if followed by 1-2 digits at end
        frac = text[comma + 1:]
        if len(frac) <= 2 and frac.isdigit():
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
    elif dot != -1:
        frac = text[dot + 1:]
        if len(frac) <= 2 and frac.isdigit():
            pass  # already decimal
        else:
            text = text.replace(".", "")
    text = text.replace(" ", "").replace(" ", "")
    try:
        return Decimal(text)
    except Exception:
        return None


def parse_date(raw: str) -> date | None:
    """Parse date strings in common formats:
    - DD.MM.YYYY / DD.MM.YY
    - DD month YYYY (Russian month names)
    - YYYY-MM-DD
    """
    text = raw.strip().strip('"').strip()
    # ISO
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # DD.MM.YYYY or DD.MM.YY
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    # DD month YYYY
    m = re.match(r"(\d{1,2})\s+(\w+)\s+(\d{4})", text)
    if m:
        d, mon, y = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        if mon in MONTHS_RU:
            try:
                return date(y, MONTHS_RU[mon], d)
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass
class Entity:
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
    def __init__(self, seed: int = 0) -> None:
        self._sampler = MnemonicSampler(seed)
        self.entities: list[Entity] = []

    def add(
            self,
            type_: str,
            raw: str,
            unit: str,
            norm: str,
            file: str,
            role: str,
            span: tuple[int, int],
            loc: str = "body",
            source_text: str = "",
    ) -> Entity:
        num = len(self.entities)
        mnemonic = self._sampler.next()
        value = self._normalize(type_, raw, norm)
        ent = Entity(
            num=num,
            mnemonic=mnemonic,
            type=type_,
            raw=raw,
            value=value,
            unit=unit,
            norm=norm,
            file=file,
            role=role,
            span=span,
            loc=loc,
            source_text=source_text,
        )
        self.entities.append(ent)
        return ent

    def _normalize(self, type_: str, raw: str, norm: str) -> str | int | None:
        if type_ == "date":
            d = parse_date(raw)
            return d.toordinal() if d else None
        if type_ == "amount":
            v = parse_amount(raw)
            if v is None:
                return None
            mult = Decimal(norm)
            return str(v * mult)
        if type_ in ("number", "pct", "ratio"):
            if type_ == "pct":
                raw = raw.strip().removesuffix("%").strip()
            if type_ == "ratio":
                parts = re.split(r"[:/]", raw)
                if len(parts) == 2:
                    numerator, denominator = (parse_amount(part) for part in parts)
                    if numerator is not None and denominator is not None and denominator != 0:
                        return str(numerator / denominator)
            v = parse_amount(raw)
            return str(v) if v is not None else raw.strip()
        return raw.strip()

    def to_dict(self) -> dict:
        return {
            "entities": [e.to_dict() for e in self.entities],
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )


def build_registry(
        tagged_docs: dict[str, tuple[str, str]],  # h8 -> (role, tagged_text)
        seed: int = 0,
) -> EntityRegistry:
    """Parse <ent> tags from every tagged doc and build the global registry.

    tagged_docs: {h8: (role, tagged_markdown)}. Entities found inside the
    filename portion (first line, starting with '# ') get loc='filename'.
    """
    reg = EntityRegistry(seed)
    for h8, (role, text) in sorted(tagged_docs.items()):
        for m in ENT_RE.finditer(text):
            attrs = parse_tag_attrs(m.group(1))
            t = attrs.get("t", "number")
            u = attrs.get("u", "RUB" if t == "amount" else "")
            n = attrs.get("n", "1")
            raw = m.group(2)
            # crude filename detection: tag starts before first newline of '# ' line
            line_start = text.rfind("\n", 0, m.start()) + 1
            loc = "body"
            if text[line_start: line_start + 2] == "# ":
                loc = "filename"
            entity = reg.add(
                type_=t,
                raw=raw,
                unit=u,
                norm=n,
                file=h8,
                role=role,
                span=(m.start(), m.end()),
                loc=loc,
                source_text=raw,
            )
            # Application-reviewed tags already own their report-local identity.
            # Draft parsing remains available for normalization and standalone runs.
            if "id" in attrs and "mnemonic" in attrs:
                entity.num = int(attrs["id"])
                entity.mnemonic = attrs["mnemonic"]
            if "value" in attrs:
                value = attrs["value"]
                if t == "date":
                    normalized_date = parse_date(value)
                    entity.value = normalized_date.toordinal() if normalized_date else None
                else:
                    entity.value = value if value else None
    return reg
