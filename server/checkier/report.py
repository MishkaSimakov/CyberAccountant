"""Console rendering adapter for verification result data."""

from __future__ import annotations

import re


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
    # the audit only cares about relations touching the card; the rest are
    # data-internal contradictions — reported as a count, not in full
    on_card = [r for r in interesting if r.get("touches_card")]
    off_card = [r for r in interesting if not r.get("touches_card")]
    # id -> cell/constant info, for annotating equations with what each
    # mnemonic actually is (raw value, type, source file)
    id_map: dict[str, dict] = {}
    for c in report["card"]["cells"] + report.get("constants", []):
        id_map[str(c.get("mnemonic") or c.get("id"))] = c

    def annotate(equation: str) -> str:
        notes = []
        for i in dict.fromkeys(re.findall(r"[a-z]+-[a-z]+", equation)):
            info = id_map.get(i)
            if info is None:
                notes.append(f"{i}=?")
                continue
            val = info.get("stated") or info.get("expected") or ""
            notes.append(
                f"{i} = {info.get('raw')!r} [{info.get('type')}"
                + (f" = {val}" if val else "")
                + f", {orig(info.get('file') or '?')}]"
            )
        return "; ".join(notes)

    lines.append("")
    lines.append(
        f"=== RELATIONS NOT PROVEN TRUE — touching card ({len(on_card)} of {len(rels)}) ==="
    )
    for r in on_card:
        status = r["status"].upper()
        tag = f"[{r['tag']}] " if r.get("tag") else ""
        files = ", ".join(orig(f) for f in r.get("files", []))
        lines.append(f"  #{r['index']} {status} {tag}{r['equation']}")
        ann = annotate(r["equation"])
        if ann:
            lines.append(f"      where: {ann}")
        if r.get("error"):
            lines.append(f"      error: {r['error']}")
        if files:
            lines.append(f"      files: {files}")

    lines.append("")
    lines.append(
        f"=== relations not touching card (skipped, {len(off_card)} of {len(rels)}): "
        f"{sum(1 for r in off_card if r['status'] == 'false')} false / "
        f"{sum(1 for r in off_card if r['status'] == 'error')} error / "
        f"{sum(1 for r in off_card if r['status'] == 'unverifiable')} unverifiable ==="
    )

    fishy = report.get("fishy", [])
    lines.append("")
    lines.append(f"=== FISHY EVIDENCE (verdict=false by construction) ({len(fishy)}) ===")
    for f in fishy:
        lines.append(f"  {f['evidence']}  (ids: {', '.join(map(str, f['ids_related']))})")

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
