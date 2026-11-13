"""Stage 2b: LLM normalization of period-style dates.

The deterministic parser handles DD.MM.YYYY and "DD month YYYY", but not
period labels like "Январь 2026" or "1 квартал 2026 г." — they get
``value = None`` and stay unknowns in the solver. This stage collects all
such date entities and asks the model for concrete dates in one batch call:

    "Январь 2026"  ->  "01.01.2026"        (first day of the period)
    "1 квартал 2026" -> "01.01.2026"
    "2026 год" -> "01.01.2026"

Every normalized entity records provenance in ``Entity.normalized_from``.
"""

from __future__ import annotations

import logging
from typing import Callable

from checkier.config import PipelineConfig
from checkier.entities import Entity, EntityRegistry, parse_date
from checkier.llm import call_llm_json
from checkier.schemas import DATE_NORM_SCHEMA

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


def _build_prompt(raws: list[str]) -> str:
    return DATE_NORM_PROMPT.replace("%%RAWS%%", "\n".join(f"- {r}" for r in raws))


def run_date_normalization(
        cfg: PipelineConfig,
        registry: EntityRegistry,
        on_token: Callable[[str], None] | None = None,
        logger: logging.Logger | logging.LoggerAdapter | None = None,
) -> list[Entity]:
    """Normalize date entities whose raw text the deterministic parser could
    not handle. Mutates the registry (sets ``value`` + ``normalized_from``);
    returns the list of changed entities. Persists nothing — the caller
    saves the registry if it wants to keep the result.
    """
    cands = [e for e in registry.entities if e.type == "date" and e.value is None]
    if not cands:
        return []
    raws = sorted({e.raw for e in cands})
    data = call_llm_json(
        _build_prompt(raws),
        model=cfg.tag_model,
        schema=DATE_NORM_SCHEMA,
        schema_name="date_normalizations",
        timeout=cfg.request_timeout,
        max_retries=cfg.max_retries,
        on_token=on_token,
        reasoning=cfg.reasoning,
        logger=logger,
    )
    entries = data if isinstance(data, list) else data.get("normalizations", [])
    by_raw: dict[str, str] = {}
    for item in entries:
        if isinstance(item, dict) and item.get("raw") and item.get("normalized"):
            by_raw[str(item["raw"])] = str(item["normalized"]).strip()
    changed: list[Entity] = []
    for e in cands:
        norm = by_raw.get(e.raw)
        if not norm:
            continue
        d = parse_date(norm)
        if d is None:
            continue
        e.value = d.toordinal()
        e.normalized_from = e.raw
        changed.append(e)
    return changed
