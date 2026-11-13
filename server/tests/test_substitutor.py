"""Requirements are checked against recorded facts, independently of inference."""

from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from checkier.entities import EntityRegistry
from checkier.formula import Id, Num, Str, collect_ids, parse
from checkier.solver import Relation, solve_system
from checkier.substitutor import Substitutor


def entity(registry, raw="110", *, kind="amount", role="card", norm="1"):
    return registry.add(type_=kind, raw=raw, unit="", norm=norm,
                        file=role, role=role, span=(0, 0))


@pytest.mark.parametrize("role", ["card", "data", "request"])
@pytest.mark.parametrize("style", ["numeric", "mnemonic"])
def test_all_roles_use_recorded_values(role, style):
    registry = EntityRegistry()
    value = entity(registry, role=role)
    key = f"#{value.num}" if style == "numeric" else value.mnemonic
    relation = Relation(0, f"{key} == 100", tag="agg", source="manual")
    Substitutor(registry).check([relation])
    assert relation.status == "false"
    assert relation.error
    assert relation.involved_files == [role]
    assert relation.touches_card is (role == "card")
    assert (relation.tag, relation.source) == ("agg", "manual")
    assert collect_ids(relation.ast) == {value.num if style == "numeric" else value.mnemonic}


@pytest.mark.parametrize("reverse", [False, True])
def test_requirements_do_not_change_each_others_results(reverse):
    registry = EntityRegistry()
    entity(registry)
    inputs = [{"equation": "#0 == 100"}, {"equation": "#0 == 110"}]
    if reverse:
        inputs.reverse()
    relations, _, _, _ = solve_system(inputs, registry)
    assert {r.equation: r.status for r in relations} == {"#0 == 100": "false", "#0 == 110": "true"}


@pytest.mark.parametrize("kind, raw, literal", [
    ("amount", "100", "100"),
    ("date", "01.01.2026", "01.01.2026"),
    ("contract", "TARGET", '"TARGET"'),
])
@pytest.mark.parametrize("role", ["card", "data"])
def test_missing_values_are_not_inferred_from_requirements(kind, raw, literal, role):
    registry = EntityRegistry()
    value = entity(registry, raw, kind=kind, role=role)
    value.value = None
    relations, _, _, _ = solve_system([
        {"equation": f"#0 == {literal}"},
        {"equation": f"#0 == {literal} && #0 == #0"},
    ], registry)
    assert [r.status for r in relations] == ["unverifiable", "unverifiable"]
    assert all("no recorded value" in r.error for r in relations)
    assert value.value is None


@pytest.mark.parametrize("stated, expected", [("50", "true"), ("150", "false")])
def test_ranges_use_recorded_values_without_needing_a_unique_solution(stated, expected):
    registry = EntityRegistry()
    entity(registry, stated)
    relations, _, verdicts, _ = solve_system([{"equation": "0 <= #0 && #0 <= 100"}], registry)
    assert relations[0].status == expected
    assert verdicts[0].status == "unverified"


def test_normalized_and_raw_values_stay_distinct_in_both_orders():
    registry = EntityRegistry()
    entity(registry, "0,11", norm="1000")
    relations, _, _, _ = solve_system([
        {"equation": "#0 == 110 && norm(#0) == 110 && raw(#0) == 0.11"},
        {"equation": "raw(#0) == 0.11 && norm(#0) == 110 && #0 == 110"},
        {"equation": "raw(#0) == norm(#0)"},
    ], registry)
    assert [r.status for r in relations] == ["true", "true", "false"]


def test_raw_value_can_be_checked_when_normalized_value_is_missing():
    registry = EntityRegistry()
    value = entity(registry, "10")
    value.value = None
    relations = [Relation(0, "raw(#0) == 10"), Relation(1, "norm(#0) == 10")]
    Substitutor(registry).check(relations)
    assert [r.status for r in relations] == ["true", "unverifiable"]


def test_date_string_and_decimal_values():
    registry = EntityRegistry()
    entity(registry, "15.01.2026", kind="date")
    entity(registry, "001", kind="docid")
    entity(registry, "0.1", role="data")
    entity(registry, "0.2", role="data")
    relations, _, _, _ = solve_system([
        {"equation": '01.01.2026 <= #0 && #0 <= 31.01.2026 && #1 == "001"'},
        {"equation": "#2 + #3 == 0.3"},
    ], registry)
    assert [r.status for r in relations] == ["true", "true"]
    assert registry.entities[0].value == date(2026, 1, 15).toordinal()


@pytest.mark.parametrize("equation", [
    "#999 == 1", "#0 + 1", "(#0 == 110", "32.01.2026 == #0",
    "#0 / 0 == 1", "0 / 0 == 0", "#0 == 110 && 1",
    '("A" == "A") == 1', '(1 == 1) + 2 == 3', '"a" + "b" == "ab"',
    '"a" < "b"', '"a" == 1',
])
def test_invalid_requirements_are_errors_and_do_not_block_other_checks(equation):
    registry = EntityRegistry()
    entity(registry)
    relations, _, _, _ = solve_system([
        {"equation": equation}, {"equation": "#0 == 110"},
    ], registry)
    assert relations[0].status == "error"
    assert relations[0].error
    assert relations[1].status == "true"


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_values_are_errors(raw):
    registry = EntityRegistry()
    value = entity(registry)
    value.value = raw
    relations = [Relation(0, "#0 == 110"), Relation(1, "1 == 1")]
    Substitutor(registry).check(relations)
    assert relations[0].status == "error"
    assert relations[1].status == "true"


def test_unknown_ids_are_errors_even_when_another_value_is_missing():
    registry = EntityRegistry()
    entity(registry).value = None
    relations = [Relation(0, "#0 == #999")]
    Substitutor(registry).check(relations)
    assert relations[0].status == "error"
    assert "999" in relations[0].error


def test_substitution_preserves_ast_registry_and_string_literals():
    registry = EntityRegistry()
    value = entity(registry, 'a "quoted" value', kind="docid")
    ast = parse('#0 == "#0"')
    original_ast, original_registry = deepcopy(ast), deepcopy(registry.to_dict())
    substitutor = Substitutor(registry)
    substituted = substitutor.substitute(ast)
    assert substituted.left == Str(value.value)
    assert substituted.right == Str("#0")
    assert ast == original_ast
    assert registry.to_dict() == original_registry
    assert substitutor.substitute(Id(value.mnemonic)) == Str(value.value)


def test_norm_uses_explicit_reviewed_value_without_multiplying_it_again():
    registry = EntityRegistry()
    value = entity(registry, "1", norm="1000")
    value.value = "2000"
    assert Substitutor(registry).substitute(Id(value.num, func="norm")) == Num(Decimal(2000))
