"""Stage 2b: fallback LLM normalization of unresolved date intervals."""

from __future__ import annotations

from typing import Callable

from checkier.config import PipelineConfig
from checkier.entities import Entity, EntityRegistry, parse_date
from checkier.llm import call_llm_json
from checkier.schemas import DATE_NORM_SCHEMA

DATE_NORM_PROMPT = """\
You normalize Russian accounting date and period expressions to inclusive
calendar-day intervals.

Rules:
- A full date has equal start/end bounds.
- "Month YYYY" -> first through last day of that month.
- "N квартал YYYY" -> first through last day of that quarter.
- "YYYY год" / just "YYYY" -> 01.01.YYYY through 31.12.YYYY.
- start/end use DD.MM.YYYY. If unresolved, BOTH must be null.
- If an expression lacks a year or is ambiguous, return null/null. Do not
  invent missing date parts. Never return only one bound or reverse the bounds.

Return a JSON array with ONE entry per input raw string.

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
) -> list[Entity]:
    """Resolve both date bounds, retaining ``normalized_from`` provenance."""
    cands = [
        e for e in registry.entities
        if e.type == "date" and (e.value is None or e.end_value is None)
    ]
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
    )
    entries = data if isinstance(data, list) else data.get("normalizations", [])
    by_raw: dict[str, tuple[str | None, str | None]] = {}
    for item in entries:
        if isinstance(item, dict) and item.get("raw"):
            start, end = item.get("start"), item.get("end")
            if (start is None) != (end is None):
                raise ValueError(f"incomplete normalized date interval for {item['raw']!r}")
            if start is not None and (not isinstance(start, str) or not isinstance(end, str)):
                raise ValueError(f"invalid normalized date interval for {item['raw']!r}")
            if start is not None and end is not None:
                start_day, end_day = parse_date(start), parse_date(end)
                if start_day is None or end_day is None:
                    raise ValueError(f"invalid normalized date interval for {item['raw']!r}")
                if start_day > end_day:
                    raise ValueError(f"reversed normalized date interval for {item['raw']!r}")
            by_raw[str(item["raw"])] = (start, end)
    changed: list[Entity] = []
    for e in cands:
        bounds = by_raw.get(e.raw)
        if not bounds or bounds == (None, None):
            continue
        start_raw, end_raw = bounds
        start, end = parse_date(start_raw), parse_date(end_raw)
        # Semantic validation above guarantees both are resolved here.
        e.value = start.toordinal()
        e.end_value = end.toordinal()
        e.normalized_from = e.raw
        changed.append(e)
    return changed
