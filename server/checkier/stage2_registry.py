"""Stage 2: build the global entity registry from tagged docs."""

from __future__ import annotations

import logging
from datetime import date
from html import escape

from checkier.config import PipelineConfig
from processing.common.llm import ModelClient
from checkier.entities import ENT_RE, EntityRegistry, build_registry, parse_tag_attrs
from checkier.stage2_dates import run_date_normalization


def run_registry(
        cfg: PipelineConfig, tagged_docs: dict[str, tuple[str, str]]
) -> EntityRegistry:
    reg = build_registry(tagged_docs, seed=cfg.seed)
    reg.save(cfg.entities_path)
    return reg


def normalize_document(cfg: PipelineConfig, tagged: str, *, client: ModelClient,
                       logger: logging.Logger | logging.LoggerAdapter | None = None) -> str:
    """Add normalized values to extracted tags for review, without saving files.

    Dates use ISO format for review; registries retain their ordinal values.
    Unresolved values are empty, and original document text stays unchanged.
    """
    registry = build_registry({"document": ("data", tagged)}, seed=cfg.seed)
    run_date_normalization(cfg, registry, client=client, logger=logger)
    entries = iter(registry.entities)

    def replace(match):
        entry = next(entries)
        value = entry.value
        if entry.type == "date" and value is not None:
            value = date.fromordinal(int(value)).isoformat()
        attributes = parse_tag_attrs(match.group(1))
        attributes["value"] = "" if value is None else str(value)
        markup = " ".join(f'{key}="{escape(value, quote=True)}"' for key, value in attributes.items())
        return f"<ent {markup}>{match.group(2)}</ent>"

    return ENT_RE.sub(replace, tagged)
