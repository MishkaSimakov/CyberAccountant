"""Pipeline orchestration: run stages in order, cache intermediate artifacts."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from checkier.config import PipelineConfig
from processing.common.llm import ModelClient
from processing.common.registry import EntityRegistry
from processing.verification.results import build_report
from checkier.solver import solve_system
from checkier.stage0_ingest import run_ingest
from checkier.stage1_tag import run_tag
from checkier.entity_adapter import run_registry, normalize_dates
from checkier.stage3_replace import run_replace
from checkier.stage5_relations import run_relations


class Pipeline:
    """High-level orchestrator. Each stage method can be called individually
    (for notebook exploration); artifacts are cached under ``out_dir``."""

    def __init__(
            self,
            cfg: PipelineConfig,
            *,
            client: ModelClient,
            logger: logging.Logger | logging.LoggerAdapter | None = None,
            mapping: dict | None = None,
            tagged: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        """Accept completed extraction artifacts to resume at registry building.

        Supplied documents are saved in the usual stage layout so scope
        detection can read them without ingesting or tagging again.
        """
        self.client = client
        self.cfg = cfg
        self.logger = logger if logger is not None else logging.getLogger(__name__)
        self._mapping = mapping
        self._tagged = tagged
        if mapping is not None:
            cfg.out_dir.mkdir(parents=True, exist_ok=True)
            cfg.mapping_path.write_text(
                json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if tagged is not None:
            cfg.tagged_dir.mkdir(parents=True, exist_ok=True)
            for file_id, (_role, text) in tagged.items():
                (cfg.tagged_dir / f"{file_id}.md").write_text(text, encoding="utf-8")
        self._registry: EntityRegistry | None = None
        self._replaced: dict[str, str] | None = None
        self._scope = {"target_row": None, "target_label": None}
        self._relations_raw: list[dict] | None = None

    # -- individual stages ---------------------------------------------------
    def ingest(self) -> dict:
        self._mapping = run_ingest(self.cfg)
        return self._mapping

    def tag(self, on_progress=None, on_token=None) -> dict[str, tuple[str, str]]:
        mapping = self._mapping or self._load_mapping()
        # use cache if all non-dup files already tagged
        if self._tagged is not None and all(
                (self.cfg.tagged_dir / f"{h8}.md").exists()
                for h8 in mapping
                if not mapping[h8].get("duplicate_of")
        ):
            return self._tagged
        self._tagged = run_tag(
            self.cfg, mapping, on_progress=on_progress, on_token=on_token, client=self.client, logger=self.logger
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
        self.registry()
        self._registry, changed = normalize_dates(self.cfg, self._tagged, on_token=on_token,
                                                  client=self.client, logger=self.logger)
        return changed

    def replace(self) -> dict[str, str]:
        if self._replaced is not None:
            return self._replaced
        tagged = self._tagged or self.tag()
        reg = self._registry or self.registry()
        self._replaced = run_replace(self.cfg, tagged, reg)
        return self._replaced

    def scope(self) -> dict:
        """Return target-row metadata; requirements are evaluated by the solver."""
        return self._scope

    def relations(self) -> list[dict]:
        if self._relations_raw is not None:
            return self._relations_raw
        replaced = self._replaced or self.replace()
        reg = self._registry or self.registry()
        scope = self._scope or self.scope()
        self._relations_raw = run_relations(self.cfg, replaced, reg, scope, client=self.client, logger=self.logger)
        return self._relations_raw

    def solve(self):
        reg = self._registry or self.registry()
        relations_raw = self._relations_raw or self.relations()
        scope = self._scope or self.scope()
        # only entities of the requested card row stay variables; all other
        # card entities are trusted as constants
        return solve_system(
            relations_raw, reg, variable_keys=scope.get("target_row")
        )

    def report(self) -> dict:
        mapping = self._mapping or self._load_mapping()
        reg = self._registry or self.registry()
        relations, fishy, verdicts, dangling = self.solve()
        scope = self._scope or self.scope()
        report = build_report(
            mapping, reg, relations, fishy, verdicts, dangling, scope, id_style=self.cfg.id_style
        )
        self.cfg.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return report

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
