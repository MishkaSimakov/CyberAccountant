"""Entity extraction and period normalization prompts."""

DATE_FORMAT_EXAMPLES = [
    "10.05.2026 or 10.05.26",
    "2026-05-10",
    "10 мая 2026 or 10 мая 2026 г.",
    '"10" мая 2026 or «10» мая 2026 (including the quotation marks)',
    "Май 2026 or мая 2026",
    "2 квартал 2026",
    "2026 год when it is used as a period label",
]
DATE_FORMAT_EXAMPLES_TEXT = "\n".join(f"- {example}" for example in DATE_FORMAT_EXAMPLES)

TAG_PROMPT = f"""\
You extract numerical entities from accounting documents.

Entity types:
- amount    - monetary sums: "245 000,00 руб.", "10 862 418,06"
- date      - calendar dates: "28.01.2026", "Январь 2026", "16 августа 2025 г."
- docid     - document / registry identifiers: "ВЕД-ЗП-2026-01", "УПР-55", "КЛ-31", "3/1"
- contract  - contract numbers: "24/03/15-01"
- number    - any other plain number (counts, codes, coefficients, table cells)
- pct       - percentages: "30%"
- ratio     - dimensionless ratios / fractions

DATE / PERIOD FORMATS
Wrap each complete expression as ONE date entity:
{DATE_FORMAT_EXAMPLES_TEXT}

A quoted day + month + year + optional "г." is ONE date, never a separate
number and month/year entity. For a range such as 01.05.2026–10.05.2026,
wrap the two dates separately and leave the dash outside the tags.

Optional attributes for amount/date/docid entries:
- unit  - RUB (default), RUB_K (thousand rubles), USD, EUR, person_month, ...
- norm  - multiplier to base unit (1000 for RUB_K), default 1

TASK
Read the document at the bottom and output a JSON array of segments that
rebuilds it exactly.

REQUIRED OUTPUT FORMAT (no other text, no markdown fence, no commentary):
[{{"content":"...","wrap":"...","type":"..."}}, ..., {{"content":"...","wrap":"","type":"EOF"}}]

Each WRAP entry: {{"content": "text before the entity", "wrap": "ENTITY TEXT",
"type": "TYPE"}} with optional "unit" / "norm". The final entry is
{{"content": "remaining text", "wrap": "", "type": "EOF"}}.

Concatenating every "content" then its "wrap" (in array order) MUST give back
the document character-for-character.

Rules:
- Tag EVERY occurrence, including inside tables, headers and the filename title line.
- Do not tag a bare year inside running text unless it is a full date or period label.
- Keep all whitespace and punctuation exactly.
- OUTPUT ONLY THE JSON ARRAY. Do NOT repeat the document. Do NOT add prose.

EXAMPLE
Document:
```
# АКТ № ОРГ-0603

| | |
|---|---|
| **Дата** | 03.06.2026 |
| **Договор** | 24/03/15-01 |
```

Output:
```json
[
  {{"content": "# АКТ № ", "wrap": "ОРГ-0603", "type": "docid"}},
  {{"content": "\\n\\n| | |\\n|---|---|\\n| **Дата** | ", "wrap": "03.06.2026", "type": "date"}},
  {{"content": " |\\n| **Договор** | ", "wrap": "24/03/15-01", "type": "contract"}},
  {{"content": " |\\n", "wrap": "", "type": "EOF"}}
]
```

ANOTHER EXAMPLE (amounts with units)
Document:
```
Стоимость: 245 000,00 руб. за январь 2026 года.
```
Output:
```json
[
  {{"content": "Стоимость: ", "wrap": "245 000,00", "type": "amount", "unit": "RUB", "norm": "1"}},
  {{"content": " руб. за ", "wrap": "январь 2026", "type": "date"}},
  {{"content": " года.\\n", "wrap": "", "type": "EOF"}}
]
```

NOW PROCESS THIS DOCUMENT.

Filename: %%FILENAME%%

Document:
```
%%CONTENT%%
```
"""


def _build_prompt(filename: str, content: str) -> str:
    """Substitute placeholders without interpreting JSON braces in the template."""
    return TAG_PROMPT.replace("%%FILENAME%%", filename).replace("%%CONTENT%%", content)


DATE_NORM_PROMPT = """\
You normalize Russian accounting period expressions to concrete dates.

Rules:
- "Month YYYY" -> first day of that month ("Январь 2026" -> "01.01.2026")
- "N квартал YYYY" -> first day of the quarter ("2 квартал 2026" -> "01.04.2026")
- "YYYY год" / just "YYYY" -> "01.01.YYYY"
- A full date passes through unchanged.
- normalized is in DD.MM.YYYY format, or null if the date cannot be determined.
- If an expression lacks a year or is ambiguous, return null. Do not invent missing date parts.

Return a JSON array with ONE entry per input raw string:

TASK
Normalize each of the following expressions:
%%RAWS%%
"""


def date_prompt(raws: list[str]) -> str:
    return DATE_NORM_PROMPT.replace("%%RAWS%%", "\n".join(f"- {r}" for r in raws))


