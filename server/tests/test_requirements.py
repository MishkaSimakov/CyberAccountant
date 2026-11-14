"""Requirement definitions are distinct from verification outcomes."""

import pytest
from pydantic import ValidationError

from app import database
from app.models import Report
from app.schemas.requirements import AssertionDefinition, RequirementUpdate, requirement_adapter
from app.services import requirements


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
    from checkier.entities import build_registry
    from app.verification import validate_formula

    registry = build_registry({"1": ("card", '<ent t="amount">100</ent> <ent t="amount">50</ent>')})
    assert (validate_formula(equation, registry) is None) is valid


def test_manual_requirement_ids_and_edits_are_scoped_to_report(db):
    with database.session_scope() as session:
        reports = [Report(name=name, stage="requirements", processing_status="completed")
                   for name in ("First", "Second")]
        session.add_all(reports)
        session.flush()
        first, second = [report.id for report in reports]

    payload = AssertionDefinition(kind="assertion", text="Original", condition=None)
    assert [requirements.save_requirement(first, payload).id for _ in range(3)] == [1, 2, 3]
    assert [requirements.save_requirement(second, payload).id for _ in range(2)] == [1, 2]
    requirements.update_requirement(first, 1, RequirementUpdate(notes="First report", silenced=True))
    updated = requirements.save_requirement(first, payload.model_copy(update={"text": "Edited"}), 1)
    assert updated.id == 1 and updated.notes == "First report" and updated.silenced
    requirements.delete_requirement(first, 2)
    assert requirements.save_requirement(first, payload).id == 4
    assert [item.id for item in requirements.report_requirements(first).requirements] == [1, 3, 4]
    other = requirements.report_requirements(second).requirements
    assert [item.id for item in other] == [1, 2]
    assert all(item.text == "Original" and item.notes == "" and not item.silenced for item in other)


def test_concurrent_manual_requirements_receive_distinct_ids(db):
    from concurrent.futures import ThreadPoolExecutor

    with database.session_scope() as session:
        report = Report(name="Concurrent", stage="requirements", processing_status="completed")
        session.add(report)
        session.flush()
        report_id = report.id

    payload = AssertionDefinition(kind="assertion", text="Check", condition=None)
    with ThreadPoolExecutor(max_workers=4) as executor:
        created = list(executor.map(lambda _: requirements.save_requirement(report_id, payload), range(8)))
    assert sorted(item.id for item in created) == list(range(1, 9))
