"""Stage 2: build the global entity registry from tagged docs."""

from __future__ import annotations

from cyberaccountant.config import PipelineConfig
from cyberaccountant.entities import EntityRegistry, build_registry


def run_registry(
    cfg: PipelineConfig, tagged_docs: dict[str, tuple[str, str]]
) -> EntityRegistry:
    reg = build_registry(tagged_docs, seed=cfg.seed)
    reg.save(cfg.entities_path)
    return reg
