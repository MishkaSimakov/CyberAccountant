"""Requirement definitions are distinct from verification outcomes."""

import pytest
from pydantic import ValidationError

from app.schemas.requirements import requirement_adapter


@pytest.mark.parametrize("condition", [True, False, None])
def test_assertion_keeps_condition_without_a_verdict(condition):
    definition = requirement_adapter.validate_python({
        "id": 1, "kind": "assertion", "condition": condition,
        "text": "Проверка подписи", "ids_related": [0],
    }).model_dump()
    assert definition["condition"] is condition
    assert "verdict" not in definition


@pytest.mark.parametrize("outcome", [
    {"verdict": "false"}, {"error": "Unknown entity"}, {"expected": "100"},
])
def test_requirement_rejects_result_fields(outcome):
    with pytest.raises(ValidationError):
        requirement_adapter.validate_python({"id": 1, "kind": "formula", "equation": "#0 == 100", **outcome})


@pytest.mark.parametrize("equation, valid", [
    ("#0 == #1 + 25.5", True),
    ("raw(#0) >= 0 && norm(#1) != 10", True),
    ('#0 == 1 || "a" == "b"', True),
    ("01.01.2026 <= 31.12.2026", True),
    ("", False),
    ("#0 = #1", False),
    ("#0 + #1", False),
    ("#999 == #0", False),
    ("32.01.2026 == #0", False),
    ("(#0 == #1", False),
    ("#0 == #1 && 10", False),
])
def test_formula_validation_uses_parser_and_known_entities(equation, valid):
    from entity_fixtures import registry_from_drafts as build_registry
    from processing.requirements.validation import validate_formula

    registry = build_registry({"1": ("card", '<ent t="amount">100</ent> <ent t="amount">50</ent>')})
    assert (validate_formula(equation, registry) is None) is valid
