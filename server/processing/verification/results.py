"""Pure assembly of field comparisons, requirement verdicts and findings."""

from dataclasses import asdict, replace
from decimal import Decimal, InvalidOperation
from processing.common.registry import EntityRegistry
from .constraints import Fishy, Relation, Verdict
from .findings import document_findings


def _entity_id(id_style: str, e) -> str:
    return e.mnemonic if id_style == "mnemonic" else str(e.num)


def build_report(
        mapping: dict,
        registry: EntityRegistry,
        relations: list[Relation],
        fishy: list[Fishy],
        verdicts: list[Verdict],
        dangling: list[str],
        scope: dict,
        *,
        id_style: str = "numeric",
        evaluations: dict | None = None,
) -> dict:
    """Assemble consumer-compatible result data entirely in memory."""
    if evaluations is not None:
        direct_relations = []
        for relation in relations:
            identity = relation.requirement_id if relation.requirement_id is not None else relation.index
            direct = evaluations.get(identity)
            direct_relations.append(replace(
                relation, status=direct.verdict if direct else "unverifiable",
                error=direct.error if direct else "No direct evaluation supplied",
                involved_files=list(direct.files) if direct else [],
                touches_card=direct.touches_card if direct else False))
        relations = direct_relations
    by_key = {}
    for e in registry.entities:
        by_key[e.mnemonic] = e
        by_key[str(e.num)] = e
        by_key[e.num] = e

    def cell_json(v: Verdict) -> dict:
        ent = by_key.get(v.key) or by_key.get(str(v.key)) or by_key.get(v.num)
        return {
            "id": _entity_id(id_style, ent) if ent else str(v.key),
            "num": v.num,
            "mnemonic": v.mnemonic,
            "raw": v.raw,
            "type": v.type,
            "status": v.status,
            "stated": v.stated,
            "expected": v.expected,
            "diff": v.diff,
            "file": v.file,
        }

    card_verdicts = [v for v in verdicts if v.role == "card" and v.target]
    const_verdicts = [v for v in verdicts if not (v.role == "card" and v.target)]

    documents = document_findings(mapping, relations, dangling)
    dup_files, oos_files = documents["duplicates"], documents["out_of_scope"]

    rel_summary = [
        {
            "index": r.index,
            "equation": r.equation,
            "tag": r.tag,
            "source": r.source,
            "status": r.status,
            "error": r.error,
            "files": r.involved_files,
            "touches_card": r.touches_card,
        }
        for r in relations
    ]

    summary = {
        "card_cells_total": len(card_verdicts),
        "ok": sum(1 for v in card_verdicts if v.status == "ok"),
        "mismatch": sum(1 for v in card_verdicts if v.status == "mismatch"),
        "unverified": sum(1 for v in card_verdicts if v.status == "unverified"),
        "duplicates": len(dup_files),
        "out_of_scope": len(oos_files),
        "dangling": len(dangling),
        "fishy": len(fishy),
        "relations_total": len(relations),
        "relations_true": sum(1 for r in relations if r.status == "true"),
        "relations_false": sum(1 for r in relations if r.status == "false"),
        "relations_false_on_card": sum(
            1 for r in relations if r.status == "false" and r.touches_card
        ),
        "relations_unverifiable": sum(
            1 for r in relations if r.status == "unverifiable"
        ),
    }

    report = {
        "card": {
            "file": next(
                (h8 for h8, e in mapping.items() if e["role"] == "card"), None
            ),
            "target_label": scope.get("target_label"),
            "target_ids": [_entity_id(id_style, v) for v in card_verdicts],
            "cells": [cell_json(v) for v in card_verdicts],
        },
        "constants": [cell_json(v) for v in const_verdicts],
        "files": documents,
        "fishy": [
            {
                "evidence": f.evidence,
                "ids_related": f.ids_related,
                "verdict": f.verdict,
            }
            for f in fishy
        ],
        "relations": rel_summary,
        "summary": summary,
    }
    if evaluations is not None:
        report["requirement_results"] = [asdict(result) for result in evaluations.values()]
        summary.update(requirements_total=len(evaluations), **{
            f"requirements_{status}": sum(result.verdict == status for result in evaluations.values())
            for status in ("true", "false", "unverifiable", "error")})
    by_num = {entity.num: entity for entity in registry.entities}
    for cell in report["card"]["cells"] + report["constants"]:
        cell["unit"] = by_num[cell["num"]].unit
    return report


def compare_fields(registry: EntityRegistry, expected_values: dict, target_ids: set[int]) -> list[Verdict]:
    """Compare stated values with unique inferred values without using solver acceptance."""
    verdicts = []
    for entity in registry.entities:
        target = entity.num in target_ids
        stated = str(entity.value) if entity.value is not None else None
        expected = expected_values.get(entity.num)
        difference = None
        status = "unverified" if target else "constant"
        if target and expected is not None and stated is not None:
            if isinstance(expected, Decimal):
                try:
                    numeric = Decimal(stated)
                except InvalidOperation:
                    status = "mismatch"
                else:
                    if numeric.is_finite():
                        status = "ok" if expected == numeric else "mismatch"
                        difference = str(abs(expected - numeric)) if status == "mismatch" else None
            else:
                status = "ok" if str(expected) == stated else "mismatch"
        verdicts.append(Verdict(
            key=entity.mnemonic, num=entity.num, mnemonic=entity.mnemonic, role=entity.role,
            file=entity.file, raw=entity.raw, type=entity.type, status=status, stated=stated,
            expected=str(expected) if expected is not None else None, diff=difference, target=target))
    return verdicts
