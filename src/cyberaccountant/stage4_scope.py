"""Stage 4: deterministic scope checks (contract / period / target row).

Emits tagged relations for every data doc:
  - contract: "<doc-contract-id> == \"24/03/15-01\""
  - period:   "01.01.2026 <= <doc-date> && <doc-date> <= 31.05.2026"

Docs failing either check are tagged ``oos`` with a reason and excluded from
relation extraction. Card and request docs always stay in scope.

The target contract and period are read from the request README's tagged
entities (they are constants — truth by definition).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from cyberaccountant.config import PipelineConfig
from cyberaccountant.entities import Entity, EntityRegistry
from cyberaccountant.formula import FormulaError, evaluate, parse


def _fmt_date(ordinal: int) -> str:
    d = date.fromordinal(int(ordinal))
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def run_scope(
    cfg: PipelineConfig,
    registry: EntityRegistry,
    mapping: dict,
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

    target_contract: str | None = None
    if contracts:
        # majority / first — they should all agree (request is truth)
        target_contract = contracts[0].raw.strip()
    period: list[int] | None = None
    if len(dates) >= 2:
        vals = sorted(int(d.value) for d in dates if d.value is not None)
        if len(vals) >= 2:
            period = [vals[0], vals[-1]]

    # --- build scope relations per data doc --------------------------------
    scope_relations: list[dict] = []
    oos: dict[str, dict] = {}
    in_scope: list[str] = []
    key = lambda e: e.mnemonic if cfg.id_style == "mnemonic" else str(e.num)  # noqa: E731

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

    result = {
        "target_contract": target_contract,
        "target_period": period,
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
        if k == entity_key or str(e.num) == str(entity_key):
            return e.raw.strip().strip('"') == target.strip()
    return True  # unknown entity: don't fail scope on it
