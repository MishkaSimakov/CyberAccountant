"""Pipeline orchestration: run stages in order, cache intermediate artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from checkier.config import PipelineConfig
from checkier.entities import EntityRegistry
from checkier.report import build_report
from checkier.stage0_ingest import run_ingest
from checkier.stage1_tag import run_tag
from checkier.stage2_registry import run_registry
from checkier.stage2_dates import run_date_normalization
from checkier.stage3_replace import run_replace
from checkier.stage4_scope import run_scope
from checkier.stage5_relations import run_relations, solve_with_repairs


class Pipeline:
    """High-level orchestrator. Each stage method can be called individually
    (for notebook exploration); artifacts are cached under ``out_dir``."""

    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg
        self._mapping: dict | None = None
        self._tagged: dict[str, tuple[str, str]] | None = None
        self._registry: EntityRegistry | None = None
        self._replaced: dict[str, str] | None = None
        self._scope: dict | None = None
        self._relations_raw: list[dict] | None = None

    # -- individual stages ---------------------------------------------------
    def ingest(self) -> dict:
        self._mapping = run_ingest(self.cfg)
        return self._mapping

    def tag(self, on_progress=None, on_token=None) -> dict[str, tuple[str, str]]:
        mapping = self._mapping or self._load_mapping()
        # use cache if all non-dup files already tagged
        if self._tagged and all(
            (self.cfg.tagged_dir / f"{h8}.md").exists()
            for h8 in mapping
            if not mapping[h8].get("duplicate_of")
        ):
            return self._tagged
        self._tagged = run_tag(
            self.cfg, mapping, on_progress=on_progress, on_token=on_token
        )
        return self._tagged

    def registry(self) -> EntityRegistry:
        if self._registry is not None:
            return self._registry
        tagged = self._tagged or self.tag()
        self._registry = run_registry(self.cfg, tagged)
        return self._registry

    def normalize_dates(self, on_token=None) -> list:
        """LLM-normalize period-style date raws ('Январь 2026' -> ordinal of
        01.01.2026) for date entities the deterministic parser could not
        handle. Saves the registry; call after ``registry()``."""
        reg = self._registry or self.registry()
        changed = run_date_normalization(self.cfg, reg, on_token=on_token)
        if changed:
            reg.save(self.cfg.entities_path)
        return changed

    def replace(self) -> dict[str, str]:
        if self._replaced is not None:
            return self._replaced
        tagged = self._tagged or self.tag()
        reg = self._registry or self.registry()
        self._replaced = run_replace(self.cfg, tagged, reg)
        return self._replaced

    def scope(self) -> dict:
        if self._scope is not None:
            return self._scope
        reg = self._registry or self.registry()
        mapping = self._mapping or self._load_mapping()
        self._scope = run_scope(self.cfg, reg, mapping)
        return self._scope

    def relations(self) -> list[dict]:
        if self._relations_raw is not None:
            return self._relations_raw
        replaced = self._replaced or self.replace()
        reg = self._registry or self.registry()
        scope = self._scope or self.scope()
        self._relations_raw = run_relations(self.cfg, replaced, reg, scope)
        return self._relations_raw

    def solve(self):
        reg = self._registry or self.registry()
        relations_raw = self._relations_raw or self.relations()
        scope = self._scope or self.scope()
        # only entities of the requested card row stay variables; all other
        # card entities are trusted as constants. Erroring LLM equations are
        # fed back to the model and re-solved (bounded by cfg.max_retries).
        return solve_with_repairs(
            self.cfg,
            relations_raw,
            reg,
            scope,
            variable_keys=scope.get("target_row"),
        )

    def report(self) -> dict:
        mapping = self._mapping or self._load_mapping()
        reg = self._registry or self.registry()
        relations, fishy, verdicts, dangling = self.solve()
        scope = self._scope or self.scope()
        return build_report(
            self.cfg, mapping, reg, relations, fishy, verdicts, dangling, scope
        )

    def run_all(self) -> dict:
        self.ingest()
        self.tag()
        self.registry()
        self.replace()
        self.scope()
        self.relations()
        return self.report()

    # -- helpers ---------------------------------------------------------------
    def _load_mapping(self) -> dict:
        if self.cfg.mapping_path.exists():
            self._mapping = json.loads(
                self.cfg.mapping_path.read_text(encoding="utf-8")
            )
            return self._mapping
        return self.ingest()
