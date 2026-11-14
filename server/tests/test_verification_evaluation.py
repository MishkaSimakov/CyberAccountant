"""Stated-value semantics, independent of expected-value inference."""

import pytest

from entity_fixtures import EntityRegistry
from processing.common.requirements import AssertionDefinition, FormulaDefinition
from processing.verification.evaluation import SavedRequirement, evaluate_requirements


def result(equation, *, missing=False):
    registry = EntityRegistry()
    entity = registry.add(type_="amount", raw="0.11", norm="1000", unit="RUB", file="1",
                          role="card", span=(0, 0))
    if missing:
        entity.value = None
    return evaluate_requirements([SavedRequirement(42, FormulaDefinition(equation=equation))], registry)[42]


@pytest.mark.parametrize("equation, verdict", [
    ("#0 == 110 && norm(#0) == 110 && raw(#0) == 0.11", "true"),
    ("#0 == 100", "false"),
    ("0.1 + 0.2 == 0.3", "true"),
    ("#0 == #0", "unverifiable"),
    ("#0 == 1 && 1 == 2", "false"),
    ("#0 == 1 || 1 == 1", "true"),
    ("1 == 2 && #0 == 1", "false"),
    ("1 == 1 || #0 == 1", "true"),
    ("#0 == 1 || 1 == 2", "unverifiable"),
    ("raw(#0) == 0.11", "true"),
])
def test_stated_values_and_missing_boolean_branches(equation, verdict):
    assert result(equation, missing="#0 == #0" in equation or "#0 == 1 " in equation or equation.endswith(
        "#0 == 1")).verdict == verdict


@pytest.mark.parametrize("equation, kind", [
    ("#999 == #999", "invalid"), ("#0 / 0 == 1", "arithmetic"),
    ('"a" == 1', "invalid"), ("#0 + 1", "invalid"), ("32.01.2026 == #0", "invalid"),
])
def test_error_categories(equation, kind):
    actual = result(equation)
    assert actual.verdict == "error"
    assert actual.error_kind == kind


@pytest.mark.parametrize("condition, verdict", [(True, "true"), (False, "false"), (None, "unverifiable")])
def test_assertion_keeps_explicit_condition(condition, verdict):
    actual = evaluate_requirements([SavedRequirement(7, AssertionDefinition(condition=condition))], EntityRegistry())
    assert actual[7].verdict == verdict
