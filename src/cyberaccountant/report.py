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
        "relations_true": sum(1 for r in relations if r.status == "true"),
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


def format_report_text(report: dict, mapping: dict | None = None) -> str:
    """Human-readable rendering of :func:`build_report` output for the console.

    Shows the solver results the JSON buries: per-card-cell verdicts with
    stated vs expected values, and every relation that came out false,
    errored or stayed unverifiable (i.e. the formulas that were checked and
    the ones the system couldn't nail down).
    """
    def orig(h8: str) -> str:
        entry = (mapping or {}).get(h8)
        return entry.get("original", h8) if entry else h8

    lines: list[str] = []
    s = report["summary"]
    lines.append("=== SUMMARY ===")
    lines.append(
        f"card cells: {s['card_cells_total']} total | {s['ok']} ok | "
        f"{s['mismatch']} mismatch | {s['unverified']} unverified"
    )
    lines.append(
        f"relations: {s['relations_total']} total | {s.get('relations_true', '?')} true | "
        f"{s['relations_false']} false | {s['relations_unverifiable']} unverifiable"
    )
    lines.append(
        f"files: {s['duplicates']} duplicates | {s['out_of_scope']} out-of-scope | "
        f"{s['dangling']} dangling | {s['fishy']} fishy"
    )

    cells = report["card"]["cells"]
    mismatches = [c for c in cells if c["status"] == "mismatch"]
    unverified = [c for c in cells if c["status"] == "unverified"]

    def cell_line(c: dict) -> str:
        base = f"[{c['type']}] {c['raw']!r} (id={c['id']}, file={orig(c['file'])})"
        extras = []
        if c.get("stated") is not None:
            extras.append(f"stated={c['stated']}")
        if c.get("expected") is not None:
            extras.append(f"expected={c['expected']}")
        if c.get("diff"):
            extras.append(f"diff={c['diff']}")
        return base + ("  " + "  ".join(extras) if extras else "")

    lines.append("")
    lines.append(f"=== CARD CELL MISMATCHES ({len(mismatches)}) ===")
    for c in mismatches:
        lines.append("  " + cell_line(c))

    lines.append("")
    lines.append(f"=== CARD CELLS UNVERIFIED — no equation pinned them down ({len(unverified)}) ===")
    for c in unverified:
        lines.append("  " + cell_line(c))

    rels = report["relations"]
    interesting = [r for r in rels if r["status"] in ("false", "error", "unverifiable")]
    lines.append("")
    lines.append(
        f"=== RELATIONS NOT PROVEN TRUE ({len(interesting)} of {len(rels)}) ==="
    )
    for r in interesting:
        status = r["status"].upper()
        tag = f"[{r['tag']}] " if r.get("tag") else ""
        files = ", ".join(orig(f) for f in r.get("files", []))
        lines.append(f"  #{r['index']} {status} {tag}{r['equation']}")
        if r.get("error"):
            lines.append(f"      error: {r['error']}")
        if files:
            lines.append(f"      files: {files}")

    fishy = report.get("fishy", [])
    lines.append("")
    lines.append(f"=== FISHY EVIDENCE (verdict=false by construction) ({len(fishy)}) ===")
    for f in fishy:
        lines.append(f"  {f['evidence']}  (ids: {', '.join(f['ids_related'])})")

    f = report["files"]
    lines.append("")
    lines.append("=== FILES ===")
    for h8, dup in f["duplicates"].items():
        lines.append(f"  duplicate: {orig(h8)} == {orig(dup)}")
    for h8, reasons in f["out_of_scope"].items():
        lines.append(f"  out-of-scope ({', '.join(reasons)}): {orig(h8)}")
    for h8 in f["dangling"]:
        lines.append(f"  dangling: {orig(h8)}")
    return "\n".join(lines)
