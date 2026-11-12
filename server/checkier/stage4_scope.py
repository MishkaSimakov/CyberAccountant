"""Stage 4: deterministic scope checks (contract / period / target row).

Emits tagged relations for every data doc:
  - contract: "<doc-contract-id> == \"24/03/15-01\""
  - period:   "01.01.2026 <= <doc-date> && <doc-date> <= 31.05.2026"

Docs failing either check are tagged ``oos`` with a reason and excluded from
relation extraction. Card and request docs always stay in scope.

The target contract and period are read from the request README's tagged
entities (they are constants — truth by definition).

Also locates the *target row* of the card («3 этап, 2026 г.»): entities on
that row stay variables for the solver; all other card entities are trusted
as constants.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal

from checkier.config import PipelineConfig
from checkier.entities import Entity, EntityRegistry
from checkier.formula import FormulaError, evaluate, parse

_TAGMARKUP_RE = re.compile(r"</?ent\b[^>]*>")
_QUOTED_RE = re.compile(r"«([^»]+)»")


def _fmt_date(ordinal: int) -> str:
    d = date.fromordinal(int(ordinal))
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def run_scope(
        cfg: PipelineConfig,
        registry: EntityRegistry,
        mapping: dict,
        *,
        target_contract: str | None = None,
        target_period: list[int] | None = None,
        target_label: str | None = None,
) -> dict:
    """Evaluate scope relations; return scope info dict and write scope.json.

    Returns:
        {
          "target_contract": str | None,
          "target_period": [start_ordinal, end_ordinal] | None,
          "scope_relations": [ {equation, tag, source}, ... ],
          "oos": {h8: {"reason": "contract"|"period", "relation_index": int}},
          "in_scope": [h8, ...],
        }
    """
    # --- find target contract & period in request entities -----------------
    contracts: list[Entity] = []
    dates: list[Entity] = []
    for e in registry.entities:
        if e.role != "request":
            continue
        if e.type == "contract":
            contracts.append(e)
        elif e.type == "date" and e.value is not None:
            dates.append(e)

    if target_contract is None and contracts:
        # majority / first — they should all agree (request is truth)
        target_contract = contracts[0].raw.strip()
    period = target_period
    if period is None and len(dates) >= 2:
        vals = sorted(int(d.value) for d in dates if d.value is not None)
        if len(vals) >= 2:
            period = [vals[0], vals[-1]]

    # --- build scope relations per data doc --------------------------------
    scope_relations: list[dict] = []
    oos: dict[str, dict] = {}
    in_scope: list[str] = []
    key = lambda e: e.mnemonic if cfg.id_style == "mnemonic" else f"#{e.num}"  # noqa: E731

    for h8, entry in sorted(mapping.items()):
        if entry["role"] != "data" or entry.get("duplicate_of"):
            continue
        doc_entities = [e for e in registry.entities if e.file == h8]
        contract_ents = [e for e in doc_entities if e.type == "contract"]
        date_ents = [
            e
            for e in doc_entities
            if e.type == "date" and e.loc == "body" and e.value is not None
        ]

        reasons: list[str] = []
        # contract check
        if target_contract and contract_ents:
            rel_eq = f'{key(contract_ents[0])} == "{target_contract}"'
            scope_relations.append(
                {"equation": rel_eq, "tag": "contract", "source": "scope"}
            )
            idx = len(scope_relations) - 1
            ok = all(
                _eval_string_eq(key(c), target_contract, registry) for c in contract_ents
            )
            if not ok:
                reasons.append("contract")
        # period check
        if period and date_ents:
            all_ok = True
            for d in date_ents:
                eq = (
                    f"{_fmt_date(period[0])} <= {key(d)} "
                    f"&& {key(d)} <= {_fmt_date(period[1])}"
                )
                scope_relations.append(
                    {"equation": eq, "tag": "period", "source": "scope"}
                )
                ok = period[0] <= int(d.value) <= period[1]
                if not ok:
                    all_ok = False
            if not all_ok:
                reasons.append("period")

        if reasons:
            oos[h8] = {"reasons": reasons}
        else:
            in_scope.append(h8)

    target_row, target_label = find_target_row_keys(cfg, registry, mapping, target_label=target_label)

    result = {
        "target_contract": target_contract,
        "target_period": period,
        "target_row": sorted(target_row) if target_row else None,
        "target_label": target_label,
        "scope_relations": scope_relations,
        "oos": oos,
        "in_scope": in_scope,
    }
    cfg.scope_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _eval_string_eq(entity_key: str, target: str, registry: EntityRegistry) -> bool:
    """String equality between an entity's raw value and a target string."""
    for e in registry.entities:
        k = e.mnemonic
        if k == entity_key or str(e.num) == str(entity_key).lstrip("#"):
            return str(e.value if e.value is not None else e.raw).strip().strip('"') == target.strip()
    return True  # unknown entity: don't fail scope on it


def find_target_row_keys(
        cfg: PipelineConfig,
        registry: EntityRegistry,
        mapping: dict,
        *,
        target_label: str | None = None,
) -> tuple[set[str | int] | None, str | None]:
    """Locate a card row by label or one-based data-row number and return its IDs.

    Numeric selection requires one monetary table and excludes its leading
    headers and column-number row. The returned label is the selected row label.

    Returns (keys, label). ``keys is None`` means targeting is disabled or
    the row could not be located — the caller should then treat ALL card
    entities as variables (old behaviour). An empty set means the row was
    found but contains no entities (defensive; same fallback applies).
    """
    # --- target spec from the request README («...» phrase) -----------------
    request_plain = ""
    for h8, entry in mapping.items():
        if entry["role"] != "request":
            continue
        p = cfg.tagged_dir / f"{h8}.md"
        if p.exists():
            request_plain += "\n" + _TAGMARKUP_RE.sub("", p.read_text(encoding="utf-8"))
    label = target_label
    # prefer the «...» phrase naming a report row (digit + "этап") — a bare
    # «...этап_3.pdf» filename quote must not win; fall back to first quote
    m = re.search(r"«([^»]*\d+\s*этап[^»]*)»", request_plain, re.IGNORECASE) or _QUOTED_RE.search(
        request_plain
    )
    if label is None and m:
        label = m.group(1).strip()
    stage_no = year = None
    if label:
        ms = re.search(r"(\d+)\s*этап", label, re.IGNORECASE)
        my = re.search(r"(20\d{2})", label)
        stage_no = ms.group(1) if ms else None
        year = my.group(1) if my else None
    if not label:
        return None, label

    # --- card lines containing "<N> этап" -----------------------------------
    card_h8 = next((h for h, e in mapping.items() if e["role"] == "card"), None)
    if card_h8 is None:
        return None, label
    card_path = cfg.tagged_dir / f"{card_h8}.md"
    if not card_path.exists():
        return None, label
    tagged = card_path.read_text(encoding="utf-8")
    lines: list[tuple[int, int, str]] = []  # (start, end, plain text)
    off = 0
    for line in tagged.splitlines(keepends=True):
        lines.append((off, off + len(line), _TAGMARKUP_RE.sub("", line)))
        off += len(line)

    if label.strip().isdecimal():
        selected = _numbered_row(lines, registry, card_h8, int(label))
        hits = [(selected[0], selected[1])]
        label = selected[2].strip().strip("|").split("|")[0].strip()
    else:
        hits = [
            (s, e) for s, e, plain in lines
            if (re.search(rf"(?<!\d){re.escape(stage_no)}\s*этап", plain, re.IGNORECASE)
                if stage_no is not None else " ".join(label.casefold().split()) in " ".join(plain.casefold().split()))
        ]
    if not hits:
        return None, label
    if year is not None:
        yr_hits = [
            (s, e) for s, e in hits
            if year in next(p for ss, ee, p in lines if (ss, ee) == (s, e))
        ]
        if yr_hits:
            hits = yr_hits

    keys = {
        e.mnemonic if cfg.id_style == "mnemonic" else e.num
        for e in registry.entities
        if e.role == "card"
           and e.file == card_h8
           and any(s <= e.span[0] and e.span[1] <= en for s, en in hits)
    }
    return (keys or None), label


def _numbered_row(lines, registry, card_file, number):
    """One-based data rows of the unique monetary table; preserve source spans."""
    if number < 1:
        raise ValueError("Номер строки должен быть положительным целым числом.")
    amounts = [e for e in registry.entities if e.file == card_file and e.type == "amount"]
    tables, rows, fenced = [], [], False
    for line in lines + [(0, 0, "")]:
        if line[2].lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        if not fenced and line[2].lstrip().startswith("|"):
            rows.append(line)
        elif rows:
            tables.append(rows)
            rows = []
    has_amount = lambda row: any(row[0] <= e.span[0] and e.span[1] <= row[1] for e in amounts)
    tables = [table for table in tables if any(has_amount(row) for row in table)]
    if len(tables) != 1:
        raise ValueError("Для выбора по номеру нужна одна таблица с денежными суммами; укажите подпись строки.")
    table = tables[0]
    data = []
    for row in table[next(i for i, row in enumerate(table) if has_amount(row)):]:
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", row[2].strip().strip("|"))]
        if all(re.fullmatch(r":?-+:?", c) for c in cells) or cells == [str(i+1) for i in range(len(cells))]:
            continue
        data.append(row)
    if number > len(data):
        raise ValueError(f"Строка №{number} не найдена: в таблице {len(data)} строк данных.")
    return data[number-1]
