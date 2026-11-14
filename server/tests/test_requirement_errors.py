"""Requirement errors expose Russian UI text and preserve English diagnostics."""

import logging

import pytest
import z3

from app import verification
from app.models import Report
from app.verification_context import VerificationContext
from checkier.config import PipelineConfig
from checkier.entities import build_registry
from checkier.file_log import file_log_run
from checkier.formula import FormulaError, parse
from checkier.solver import Relation, Solver
from checkier.substitutor import Substitutor


@pytest.mark.parametrize("equation, diagnostic, user_message", [
    ('"abc', "unterminated string", "Незакрытая строка в позиции 1. Добавьте кавычку."),
    ("# == 1", "expected digits", "После # в позиции 1 укажите номер сущности."),
    ("#0 = 1", "unexpected character", "Недопустимый символ '=' в позиции 4."),
    ("1 == 1 2", "trailing tokens", "Лишнее выражение в позиции 8."),
    ("raw(1) == 1", "expects an entity id", "В функции raw() укажите сущность в позиции 5."),
    ("(#0 == 1", "expected ')'", "Ожидается закрывающая скобка в позиции 9."),
    ("", "unexpected token", "Ожидается число, строка, дата или сущность в позиции 1."),
    ("32.01.2026 == #0", "invalid date", "Некорректная дата 32.01.2026 в позиции 1."),
])
def test_parser_errors_and_validation_keep_both_languages(equation, diagnostic, user_message, caplog):
    with pytest.raises(FormulaError) as caught:
        parse(equation)
    assert diagnostic in str(caught.value)
    assert caught.value.user_message == user_message
    assert verification.validate_formula(equation, build_registry({})) == user_message
    assert diagnostic in caplog.text
    assert user_message not in caplog.text


@pytest.mark.parametrize("equation, status, diagnostic, user_message", [
    ("#0 == 99", "false", "requirement is false", "Требование не выполняется для указанных значений."),
    ("#999 == #0", "error", "unknown entity ids", "Неизвестные сущности: 999."),
    ('#0 == "100"', "error", "type clash", "Несовместимые типы значений для оператора ==."),
    ('"a" + 1 == 1', "error", "non-numeric operands", "Оператор + требует числовых значений."),
    ("#0 / 0 == 1", "error", "DivisionByZero", "Деление на ноль в формуле."),
    ("0 / 0 == 1", "error", "DivisionUndefined", "Не удалось выполнить арифметическую операцию в формуле."),
    ("#1 == 1", "unverifiable", "no recorded value",
     "У сущности 1 не задано значение. Недостаточно данных для проверки."),
    ("start(#0) == 01.01.2026", "error", "requires a date entity",
     "Функция start() требует сущность с типом «дата»: 0."),
    ("end(#2) == 01.01.2026", "unverifiable", "no recorded end date bound",
     "У сущности 2 не задано окончание периода."),
    (None, "error", "Unknown entity IDs", "Неизвестные сущности: 999."),
])
def test_verification_returns_russian_and_logs_english(tmp_path, caplog, equation, status, diagnostic, user_message):
    registry = build_registry({"1": ("card", '<ent t="amount">100</ent><ent t="amount">200</ent>'
                                             '<ent t="date">01.01.2026</ent>')})
    registry.entities[1].value = None
    registry.entities[2].end_value = None
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path)
    context = VerificationContext(cfg, {}, registry, {"target_row": None},
                                  {"1": {"role": "card"}}, Report(id=42))
    definition = (dict(kind="formula", equation=equation) if equation is not None
                  else dict(kind="assertion", condition=False, text="Check", ids_related=[999]))
    definition.update(id=7, source="manual")
    log_path = tmp_path / "verification.log"
    with caplog.at_level(logging.INFO), file_log_run(log_path, "verification"):
        result = verification.evaluate_requirements(context, [definition])
    outcome = result["requirement_results"][0]
    assert outcome["error"] == user_message
    assert outcome["verdict"] == status
    for log in (caplog.text, log_path.read_text()):
        assert diagnostic in log
        assert "Report 42, requirement 7" in log
        if equation is not None:
            assert equation in log
        assert user_message not in log
    if equation is not None:
        assert diagnostic in result["relations"][0]["error"]


def test_external_solver_error_has_russian_fallback_and_english_diagnostic(monkeypatch):
    registry = build_registry({})
    solver = Solver([Relation(0, "1 == 1")], registry)

    def fail(node):
        raise z3.Z3Exception("internal solver diagnostic")

    monkeypatch.setattr(solver, "to_z3", fail)
    solver.solve()
    relation = solver.relations[0]
    assert relation.status == "error"
    assert relation.error == "internal solver diagnostic"
    assert relation.user_error == "Не удалось проверить формулу. Проверьте выражение и значения сущностей."


def test_successful_recheck_clears_both_error_messages():
    registry = build_registry({})
    relation = Relation(0, "1 == 2")
    checker = Substitutor(registry)
    checker.check([relation])
    assert relation.error and relation.user_error
    relation.equation = "1 == 1"
    checker.check([relation])
    assert relation.status == "true"
    assert relation.error is relation.user_error is None


def test_complex_formula_validation_has_russian_fallback(monkeypatch, caplog):
    def fail(equation):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(verification, "parse", fail)
    assert verification.validate_formula("1 == 1", build_registry({})) == (
        "Формула слишком сложная. Упростите выражение.")
    assert "maximum recursion depth exceeded" in caplog.text
