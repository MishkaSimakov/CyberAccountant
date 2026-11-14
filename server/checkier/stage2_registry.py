"""Stage 2: build the global entity registry from tagged docs."""

from __future__ import annotations

from datetime import date
from html import escape

from checkier.config import PipelineConfig
from checkier.entities import ENT_RE, EntityRegistry, build_registry, parse_tag_attrs
from checkier.stage2_dates import run_date_normalization


def run_registry(
        cfg: PipelineConfig, tagged_docs: dict[str, tuple[str, str]]
) -> EntityRegistry:
    reg = build_registry(tagged_docs, seed=cfg.seed)
    reg.save(cfg.entities_path)
    return reg


def normalize_document(cfg: PipelineConfig, tagged: str) -> str:
    """Add normalized values to extracted tags for review, without saving files.

    Dates use inclusive ISO start/end timestamps for review; registries retain
    ordinal values. Legacy date-only values are accepted as one-day intervals.
    Unresolved values are empty, and original document text stays unchanged.
    """
    registry = build_registry({"document": ("data", tagged)}, seed=cfg.seed)
    run_date_normalization(cfg, registry)
    entries = iter(registry.entities)

    def replace(match):
        entry = next(entries)
        value = entry.value
        attributes = parse_tag_attrs(match.group(1))
        if entry.type == "date":
            attributes.pop("value", None)
            attributes["start"] = (
                date.fromordinal(int(value)).isoformat() + "T00:00:00"
                if value is not None else ""
            )
            attributes["end"] = (
                date.fromordinal(int(entry.end_value)).isoformat() + "T23:59:59"
                if entry.end_value is not None else ""
            )
        else:
            attributes["value"] = "" if value is None else str(value)
        markup = " ".join(f'{key}="{escape(value, quote=True)}"' for key, value in attributes.items())
        return f"<ent {markup}>{match.group(2)}</ent>"

    return ENT_RE.sub(replace, tagged)
