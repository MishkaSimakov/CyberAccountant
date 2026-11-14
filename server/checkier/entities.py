"""Entity registry: parse tagged docs, normalize values, assign global ids."""

from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from html import unescape
from pathlib import Path

from checkier.bip39 import MnemonicSampler

# ---------------------------------------------------------------------------
# Tag parsing
# ---------------------------------------------------------------------------

ENT_RE = re.compile(
    r"<ent\s+([^>]*)>(.*?)</ent>",
    re.DOTALL,
)
ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}


def parse_tag_attrs(attr_str: str) -> dict[str, str]:
    attrs = {key: unescape(value) for key, value in ATTR_RE.findall(attr_str)}
    return attrs


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


def parse_date_interval(raw: str) -> tuple[date, date] | None:
    """Resolve an exact date or complete month/quarter/year to inclusive days.

    Incomplete and ambiguous expressions return None rather than acquiring
    invented components. Quoted Russian days retain their quotation marks in
    source markup but are ignored for parsing.
    """
    text = " ".join(raw.strip().split())
    suffix = r"(?:\s*(?:г\.?|года?))?"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        try:
            day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return day, day
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", text)
    if m:
        d, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            day = date(year, month, d)
            return day, day
        except ValueError:
            return None
    m = re.fullmatch(r'["«]?(\d{1,2})["»]?\s+(\w+)\s+(\d{4})' + suffix, text, re.IGNORECASE)
    if m and m.group(2).lower() in MONTHS_RU:
        try:
            day = date(int(m.group(3)), MONTHS_RU[m.group(2).lower()], int(m.group(1)))
            return day, day
        except ValueError:
            return None
    m = re.fullmatch(r"(\w+)\s+(\d{4})" + suffix, text, re.IGNORECASE)
    if m and m.group(1).lower() in MONTHS_RU:
        year, month = int(m.group(2)), MONTHS_RU[m.group(1).lower()]
        try:
            return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
        except ValueError:
            return None
    m = re.fullmatch(r"([1-4])(?:-й|-го)?\s+квартал\s+(\d{4})" + suffix, text, re.IGNORECASE)
    if m:
        quarter, year = int(m.group(1)), int(m.group(2))
        first_month = 3 * quarter - 2
        last_month = first_month + 2
        try:
            return date(year, first_month, 1), date(year, last_month, calendar.monthrange(year, last_month)[1])
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{4})(?:\s*(?:г\.?|года?))?", text, re.IGNORECASE)
    if m:
        try:
            return date(int(m.group(1)), 1, 1), date(int(m.group(1)), 12, 31)
        except ValueError:
            return None
    return None


def parse_date(raw: str) -> date | None:
    """Return the inclusive start day for compatibility with old callers."""
    interval = parse_date_interval(raw)
    return interval[0] if interval else None


def parse_date_bound(raw: str, *, end: bool) -> date:
    """Parse a timezone-free review timestamp and enforce its boundary time."""
    try:
        value = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid date bound {raw!r}; expected YYYY-MM-DDTHH:MM:SS") from exc
    expected = time(23, 59, 59) if end else time(0, 0, 0)
    if value.time() != expected:
        raise ValueError(f"invalid date bound time in {raw!r}; expected {expected}")
    return value.date()


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
    end_value: str | int | float | None = None  # inclusive date end ordinal

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
            "end_value": self.end_value,
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
        interval = parse_date_interval(raw) if type_ == "date" else None
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
            end_value=interval[1].toordinal() if interval else None,
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
            if t == "date" and ("start" in attrs or "end" in attrs):
                if "start" not in attrs or "end" not in attrs:
                    raise ValueError(f"incomplete date interval for entity {entity.num}")
                start_raw, end_raw = attrs["start"], attrs["end"]
                if not start_raw and not end_raw:
                    entity.value = entity.end_value = None
                elif not start_raw or not end_raw:
                    raise ValueError(f"incomplete date interval for entity {entity.num}")
                else:
                    start = parse_date_bound(start_raw, end=False)
                    end = parse_date_bound(end_raw, end=True)
                    if start > end:
                        raise ValueError(f"reversed date interval for entity {entity.num}")
                    entity.value = start.toordinal()
                    entity.end_value = end.toordinal()
            elif "value" in attrs:
                value = attrs["value"]
                if t == "date":
                    # Legacy review markup stores one exact calendar day.
                    normalized_date = parse_date(value)
                    entity.value = normalized_date.toordinal() if normalized_date else None
                    entity.end_value = entity.value
                else:
                    entity.value = value if value else None
    return reg
