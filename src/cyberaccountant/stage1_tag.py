"""Stage 1: LLM-based entity tagging.

Wraps every numeric entity (amounts, dates, doc ids, contract numbers,
plain numbers, percentages, ratios) in <ent t="..." u="..." n="..."> tags.
Runs per file, in parallel, with schema-validated structured output.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from cyberaccountant.config import PipelineConfig
from cyberaccountant.llm import call_llm_json
from cyberaccountant.schemas import TAGGING_SCHEMA

TAG_PROMPT = """\
You are annotating entities in a markdown document extracted from an accounting file.

Filename: {filename}

Document:
```
{content}
```

Wrap EVERY occurrence of the following entity types in XML tags:

- amount    - monetary sums, e.g. "245 000,00 руб.", "10 862 418,06", "семьсот сорок четыре тысячи рублей 00 копеек"
- date      - calendar dates, e.g. "28.01.2026", "Январь 2026", "16 августа 2025 г."
- docid     - document / registry identifiers, e.g. "ВЕД-ЗП-2026-01", "УПР-55", "КЛ-31", "СВ-0426", "3/1"
- contract  - contract numbers, e.g. "24/03/15-01"
- number    - any other plain number (counts, codes, coefficients, table cell values)
- pct       - percentages, e.g. "30%"
- ratio     - dimensionless ratios / fractions

Tag format:
  <ent t="TYPE" u="UNIT" n="MULT">original text</ent>

Attributes:
- t: one of amount | date | docid | contract | number | pct | ratio  (REQUIRED)
- u: unit for amounts: RUB (default), RUB_K (thousand rubles), USD, EUR, person_month, ...  (optional)
- n: multiplier to convert to the base unit, as an integer or decimal (e.g. 1000 for thousand rubles)  (optional, default 1)

Rules:
- Tag EVERY occurrence, including inside tables and headers. Do not skip any.
- Do NOT change any other text; preserve the document exactly, only adding tags.
- Do not tag years inside running text unless they are part of a full date or a period label.
- For the file's own title line (the "# ..." heading), tag entities there too.
- Return the full annotated document in the "document" field.
"""


def _validate_tags(text: str) -> list[str]:
    """Return a list of structural problems (empty means OK)."""
    problems: list[str] = []
    opens = text.count("<ent ")
    closes = text.count("</ent>")
    if opens != closes:
        problems.append(f"unbalanced tags: {opens} opens vs {closes} closes")
    for m in re.finditer(r"<ent [^>]*>(.*?)</ent>", text, re.DOTALL):
        if "<ent" in m.group(1):
            problems.append("nested <ent> tags are not allowed")
            break
    return problems


def _tag_one(cfg: PipelineConfig, h8: str, original_name: str) -> str:
    src = cfg.files_dir / f"{h8}.md"
    content = src.read_text(encoding="utf-8")
    prompt = TAG_PROMPT.format(filename=original_name, content=content)
    last_err: Exception | None = None
    for attempt in range(cfg.max_retries + 1):
        try:
            data = call_llm_json(
                prompt,
                model=cfg.tag_model,
                schema=TAGGING_SCHEMA,
                schema_name="tagged_document",
                timeout=cfg.request_timeout,
            )
            doc = data["document"] if isinstance(data, dict) else data[0]["document"]
            problems = _validate_tags(doc)
            if problems:
                raise ValueError("; ".join(problems))
            return doc
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            prompt = (
                TAG_PROMPT.format(filename=original_name, content=content)
                + "\n\nPrevious attempt failed: "
                + str(last_err)[:1000]
                + "\nReturn ONLY valid JSON with the full annotated document."
            )
    raise RuntimeError(f"tagging failed for {h8}: {last_err}")


def run_tag(cfg: PipelineConfig, mapping: dict) -> dict[str, tuple[str, str]]:
    """Tag all non-duplicate files. Returns {h8: (role, tagged_text)}."""
    cfg.tagged_dir.mkdir(parents=True, exist_ok=True)
    todo = [
        (h8, entry["role"], Path(entry["original"]).name)
        for h8, entry in mapping.items()
        if not entry.get("duplicate_of")
    ]
    results: dict[str, tuple[str, str]] = {}
    with ThreadPoolExecutor(max_workers=cfg.tag_workers) as pool:
        futs = {
            pool.submit(_tag_one, cfg, h8, name): (h8, role)
            for h8, role, name in todo
        }
        for fut in as_completed(futs):
            h8, role = futs[fut]
            tagged = fut.result()
            (cfg.tagged_dir / f"{h8}.md").write_text(tagged, encoding="utf-8")
            results[h8] = (role, tagged)
    return results
