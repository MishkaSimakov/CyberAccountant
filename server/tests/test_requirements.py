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
