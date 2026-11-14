"""Notebook adapter for model requirement generation and optional artifact saving."""

import json
from dataclasses import asdict

from processing.requirements.generators.llm import generate_relations
from processing.requirements.options import RequirementOptions


def run_relations(cfg, replaced_docs, registry, scope, *, client, logger=None) -> list[dict]:
    definitions = generate_relations(RequirementOptions.from_settings(cfg), replaced_docs, registry, scope,
                                     client=client, logger=logger)
    relations = []
    for definition in definitions:
        if definition.kind == "formula":
            raw = asdict(definition)
            raw.pop("kind")
        else:
            raw = dict(evidence=definition.text, ids_related=list(definition.ids_related),
                       verdict="unverifiable" if definition.condition is None else "false", source=definition.source)
        relations.append(raw)
    cfg.relations_path.write_text(json.dumps(relations, ensure_ascii=False, indent=2), encoding="utf-8")
    return relations
