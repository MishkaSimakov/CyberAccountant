"""Stage 7: final report generation."""

from __future__ import annotations

import json

from cyberaccountant.config import PipelineConfig
from cyberaccountant.entities import EntityRegistry
from cyberaccountant.solver import Fishy, Relation, Verdict


def _entity_id(cfg: PipelineConfig, e) -> str:
    return e.mnemonic if cfg.id_style == "mnemonic" else str(e.num)


def build_report(
    cfg: PipelineConfig,
    mapping: dict,
    registry: EntityRegistry,
    relations: list[Relation],
    fishy: list[Fishy],
    verdicts: list[Verdict],
    dangling: list[str],
    scope: dict,
) -> dict:
    """Assemble the final report dict and write out/report.json."""
    by_key = {}
    for e in registry.entities:
        by_key[e.mnemonic] = e
        by_key[str(e.num)] = e
        by_key[e.num] = e

    def cell_json(v: Verdict) -> dict:
        ent = by_key.get(v.key) or by_key.get(str(v.key)) or by_key.get(v.num)
        return {
            "id": _entity_id(cfg, ent) if ent else str(v.key),
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

    card_verdicts = [v for v in verdicts if v.role == "card"]
    const_verdicts = [v for v in verdicts if v.role != "card"]

    dup_files = {
        h8: entry["duplicate_of"]
        for h8, entry in mapping.items()
        if entry.get("duplicate_of")
    }
    oos_files = {h8: info["reasons"] for h8, info in scope.get("oos", {}).items()}

    rel_summary = [
        {
            "index": r.index,
            "equation": r.equation,
            "tag": r.tag,
            "source": r.source,
            "status": r.status,
            "error": r.error,
            "files": r.involved_files,
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
        "relations_false": sum(1 for r in relations if r.status == "false"),
        "relations_unverifiable": sum(
            1 for r in relations if r.status == "unverifiable"
        ),
    }

    report = {
        "card": {
            "file": next(
                (h8 for h8, e in mapping.items() if e["role"] == "card"), None
            ),
            "cells": [cell_json(v) for v in card_verdicts],
        },
        "constants": [cell_json(v) for v in const_verdicts],
        "files": {
            "duplicates": dup_files,
            "out_of_scope": oos_files,
            "dangling": dangling,
        },
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
    cfg.report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return report
