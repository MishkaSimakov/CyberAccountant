"""Tests for the equation solver."""

from __future__ import annotations

from checkier.entities import EntityRegistry
from checkier.solver import solve_system


def _reg_with_card(cards: dict, data: dict, seed: int = 0):
    """Build a registry from {logical_key: (type, raw, value, role)} dicts.

    Returns (registry, ent_by_logical_key) where ent_by_logical_key maps the
    logical dict key to the Entity (whose ``.key`` is the assigned mnemonic).
    """
    reg = EntityRegistry(seed)
    entries = []
    for key, (type_, raw, value, role) in {**data, **cards}.items():
        entries.append((key, type_, raw, value, role))
    # add in deterministic order: data first, then card
    ordered = sorted(entries, key=lambda x: 0 if x[4] in ("data", "request") else 1)
    ent_by_key = {}
    for key, type_, raw, value, role in ordered:
        norm = "1"
        unit = "RUB" if type_ == "amount" else ""
        e = reg.add(
            type_=type_,
            raw=raw,
            unit=unit,
            norm=norm,
            file="f1" if role != "card" else "f2",
            role=role,
            span=(0, 0),
        )
        # override normalized value
        if value is not None:
            if type_ == "date":
                e.value = value  # ordinal
            elif type_ in ("amount", "number"):
                e.value = str(value)
            else:
                e.value = value
        ent_by_key[key] = e
    return reg, ent_by_key


def test_simple_equality_solves_card_variable():
    reg, ents = _reg_with_card(
        cards={"card-total": ("amount", "1000", "1000", "card")},
        data={"doc-a": ("amount", "600", "600", "data"),
              "doc-b": ("amount", "400", "400", "data")},
    )
    a, b, c = ents["doc-a"].key, ents["doc-b"].key, ents["card-total"].key
    rels = [{"equation": f"{a} + {b} == {c}"}]
    relations, fishy, verdicts, dangling = solve_system(rels, reg)
    v = next(v for v in verdicts if v.mnemonic == ents["card-total"].mnemonic)
    assert v.status == "ok"
    assert v.expected == "1000"


def test_mismatch_detected():
    reg, ents = _reg_with_card(
        cards={"card-total": ("amount", "999", "999", "card")},
        data={"doc-a": ("amount", "600", "600", "data"),
              "doc-b": ("amount", "400", "400", "data")},
    )
    a, b, c = ents["doc-a"].key, ents["doc-b"].key, ents["card-total"].key
    rels = [{"equation": f"{a} + {b} == {c}"}]
    _, _, verdicts, _ = solve_system(rels, reg)
    v = next(v for v in verdicts if v.mnemonic == ents["card-total"].mnemonic)
    assert v.status == "mismatch"
    assert v.expected == "1000"
    assert v.diff is not None


def test_unverified_when_no_relation():
    reg, ents = _reg_with_card(
        cards={"card-x": ("amount", "100", "100", "card")},
        data={"doc-a": ("amount", "600", "600", "data")},
    )
    rels = [{"equation": f"{ents['doc-a'].key} == 600"}]
    _, _, verdicts, _ = solve_system(rels, reg)
    v = next(v for v in verdicts if v.mnemonic == ents["card-x"].mnemonic)
    assert v.status == "unverified"


def test_constant_contradiction_flagged_false():
    reg, ents = _reg_with_card(
        cards={},
        data={"doc-a": ("amount", "600", "600", "data"),
              "doc-b": ("amount", "600", "600", "data")},
    )
    a, b = ents["doc-a"].key, ents["doc-b"].key
    rels = [{"equation": f"{a} == {b}"}]
    relations, _, _, _ = solve_system(rels, reg)
    assert relations[0].status == "true"

    rels2 = [{"equation": f"{a} + 1 == {b}"}]
    relations2, _, _, _ = solve_system(rels2, reg)
    assert relations2[0].status == "false"


def test_dangling_files():
    reg, ents = _reg_with_card(
        cards={"card-x": ("amount", "100", "100", "card")},
        data={"doc-a": ("amount", "600", "600", "data")},
    )
    # add an entity from a different file that is not referenced
    e = reg.add(type_="amount", raw="999", unit="RUB", norm="1",
                file="orphan", role="data", span=(0, 0))
    e.value = "999"
    rels = [{"equation": f"{ents['doc-a'].key} == 600"}]
    _, _, _, dangling = solve_system(rels, reg)
    assert "orphan" in dangling
    assert "f1" not in dangling


def test_non_numeric_number_value_does_not_crash():
    """'number' entities with unparsable raw values (e.g. '3/1') must not
    blow up Decimal conversion in Solver.__init__ — they stay strings."""
    reg = EntityRegistry(0)
    e = reg.add(type_="number", raw="3/1", unit="", norm="1",
                file="f1", role="data", span=(0, 0))
    # registry kept the raw string as value since "3/1" isn't a plain number
    assert e.value == "3/1"
    rels = [{"equation": f'{e.key} == "3/1"'}]
    relations, _, _, _ = solve_system(rels, reg)
    assert relations[0].status == "true"


def test_string_valued_entity_in_arithmetic_is_flagged_not_crash():
    """A 'number' entity whose value stayed a string (e.g. raw '3/1') used in
    arithmetic must produce a relation error naming the entity, not TypeError."""
    reg, ents = _reg_with_card(
        cards={"card-total": ("amount", "1000", "1000", "card")},
        data={"doc-num": ("number", "3", "3", "data"),
              "doc-frac": ("amount", "500", "500", "data")},
    )
    frac = reg.add(type_="number", raw="3/1", unit="", norm="1",
                   file="f3", role="data", span=(0, 0))
    assert frac.value == "3/1"
    a, c = ents["doc-num"].key, ents["card-total"].key
    rels = [{"equation": f"{frac.key} * {a} == {c}"}]
    relations, _, _, _ = solve_system(rels, reg)
    assert relations[0].status == "error"
    assert "3/1" in relations[0].error
    assert frac.key in relations[0].error


def test_joint_two_unknown_system_solves_both():
    """x + y == 1000 && x - y == 200: no single-unknown rule fires, but the
    joint system pins both — exactly what the old fixpoint could not do."""
    reg, ents = _reg_with_card(
        cards={"card-x": ("amount", "600", "600", "card"),
               "card-y": ("amount", "400", "400", "card")},
        data={},
    )
    x, y = ents["card-x"].key, ents["card-y"].key
    rels = [{"equation": f"{x} + {y} == 1000"},
            {"equation": f"{x} - {y} == 200"}]
    relations, _, verdicts, _ = solve_system(rels, reg)
    assert all(r.status == "true" for r in relations)
    by_mn = {v.mnemonic: v for v in verdicts}
    assert by_mn[ents["card-x"].mnemonic].status == "ok"
    assert by_mn[ents["card-y"].mnemonic].status == "ok"


def test_joint_system_mismatch_detected():
    reg, ents = _reg_with_card(
        cards={"card-x": ("amount", "600", "600", "card"),
               "card-y": ("amount", "999", "999", "card")},
        data={},
    )
    x, y = ents["card-x"].key, ents["card-y"].key
    rels = [{"equation": f"{x} + {y} == 1000"},
            {"equation": f"{x} - {y} == 200"}]
    _, _, verdicts, _ = solve_system(rels, reg)
    by_mn = {v.mnemonic: v for v in verdicts}
    assert by_mn[ents["card-x"].mnemonic].status == "ok"
    v = by_mn[ents["card-y"].mnemonic]
    assert v.status == "mismatch"
    assert v.expected == "400"


def test_bounded_variable_is_unverified_not_pinned():
    """A variable only constrained by a range has no unique solution."""
    reg, ents = _reg_with_card(
        cards={"card-d": ("date", "Январь 2026", None, "card")},
        data={},
    )
    d = ents["card-d"].key
    rels = [{"equation": f"01.01.2026 <= {d} && {d} <= 31.05.2026"}]
    relations, _, verdicts, _ = solve_system(rels, reg)
    assert relations[0].status == "true"  # consistent, accepted
    v = next(v for v in verdicts if v.mnemonic == ents["card-d"].mnemonic)
    assert v.status == "unverified"  # bounds don't pin a unique value


def test_and_chain_of_identities_resolves():
    """a == b && b == c with c ungrounded: jointly pins c to a's value."""
    reg, ents = _reg_with_card(
        cards={},
        data={"doc-a": ("docid", "КЛ-31", "КЛ-31", "data"),
              "doc-b": ("docid", "КЛ-31", "КЛ-31", "data"),
              "doc-c": ("docid", "КЛ-31", "КЛ-31", "data")},
    )
    a, b, c = (ents[k].key for k in ("doc-a", "doc-b", "doc-c"))
    reg.entities[2].value = None  # c ungrounded
    rels = [{"equation": f"{a} == {b} && {b} == {c}"}]
    relations, _, _, _ = solve_system(rels, reg)
    assert relations[0].status == "true"


def test_non_target_card_cells_become_constants():
    """variable_keys selects the target row; other card cells are trusted."""
    reg, ents = _reg_with_card(
        cards={"card-x": ("amount", "600", "600", "card"),
               "card-y": ("amount", "400", "400", "card")},
        data={"doc-a": ("amount", "600", "600", "data")},
    )
    a, x = ents["doc-a"].key, ents["card-x"].key
    rels = [{"equation": f"{a} == {x}"}]
    _, _, verdicts, _ = solve_system(
        rels, reg, variable_keys={ents["card-x"].mnemonic}
    )
    by_mn = {v.mnemonic: v for v in verdicts}
    assert by_mn[ents["card-x"].mnemonic].status == "ok"
    vy = by_mn[ents["card-y"].mnemonic]
    assert vy.status == "constant"  # trusted, not verified
    assert vy.target is False


def test_boolean_subexpression_compared_to_boolean():
    """('A' == 'A') == (5 > 3): folded bool operands of an outer == must not
    crash on .sort() — the LLM does write such relations."""
    reg = EntityRegistry(0)
    reg.add(type_="amount", raw="5", unit="RUB", norm="1", file="f1",
            role="data", span=(0, 0))
    rels = [{'equation': '("A" == "A") == (5 > 3)'}]
    relations, _, _, _ = solve_system(rels, reg)
    assert relations[0].status == "true"

    rels2 = [{'equation': '("A" == "B") == (5 > 3)'}]
    relations2, _, _, _ = solve_system(rels2, reg)
    assert relations2[0].status == "false"


def test_boolean_vs_number_is_type_clash_not_crash():
    reg = EntityRegistry(0)
    reg.add(type_="amount", raw="5", unit="RUB", norm="1", file="f1",
            role="data", span=(0, 0))
    rels = [{'equation': '("A" == "A") == 5'}]
    relations, _, _, _ = solve_system(rels, reg)
    assert relations[0].status == "error"
    assert "type clash" in relations[0].error
