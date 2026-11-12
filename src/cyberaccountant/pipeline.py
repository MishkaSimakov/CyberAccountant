"""Pipeline orchestration: run stages in order, cache intermediate artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from cyberaccountant.config import PipelineConfig
from cyberaccountant.entities import EntityRegistry
from cyberaccountant.report import build_report
from cyberaccountant.solver import solve_system
from cyberaccountant.stage0_ingest import run_ingest
from cyberaccountant.stage1_tag import run_tag
from cyberaccountant.stage2_registry import run_registry
from cyberaccountant.stage3_replace import run_replace
from cyberaccountant.stage4_scope import run_scope
from cyberaccountant.stage5_relations import run_relations


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
        return solve_system(relations_raw, reg)

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
