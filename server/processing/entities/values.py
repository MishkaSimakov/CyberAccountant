"""Pure parsing of source amounts, dates, and normalized values."""

import re
from datetime import date
from decimal import Decimal

from processing.common.entities import NormalizedValue, UnknownValue

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


def parse_value(type_: str, raw: str, norm: Decimal, reviewed: str | None = None) -> NormalizedValue:
    """An explicit reviewed value bypasses source scaling, including unknowns."""
    if reviewed is not None:
        if reviewed == "":
            return UnknownValue.UNKNOWN
        if type_ == "date":
            return parse_date(reviewed) or UnknownValue.UNKNOWN
        if type_ in {"amount", "number", "pct", "ratio"}:
            try:
                return Decimal(reviewed)
            except ArithmeticError:
                return reviewed
        return reviewed
    if type_ == "date":
        return parse_date(raw) or UnknownValue.UNKNOWN
    if type_ == "amount":
        amount = parse_amount(raw)
        return amount * norm if amount is not None else UnknownValue.UNKNOWN
    if type_ in {"number", "pct", "ratio"}:
        text = raw.strip().removesuffix("%").strip() if type_ == "pct" else raw
        if type_ == "ratio":
            parts = re.split(r"[:/]", text)
            if len(parts) == 2:
                numerator, denominator = (parse_amount(part) for part in parts)
                if numerator is not None and denominator is not None and denominator != 0:
                    return numerator / denominator
        value = parse_amount(text)
        return value if value is not None else text.strip()
    return raw.strip()


def recorded_value(value: NormalizedValue) -> str | int | None:
    """Project typed values for the existing requirement/solver index."""
    if value is UnknownValue.UNKNOWN:
        return None
    if isinstance(value, date):
        return value.toordinal()
    return str(value)


