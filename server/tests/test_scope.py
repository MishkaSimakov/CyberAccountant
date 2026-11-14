"""Scope requirements use report settings; evaluated failures determine exclusions."""

import re
from datetime import date

import pytest

from app import verification
from app.verification_context import VerificationContext
from app.generators.scope import generate_scope
from app.models import Report
from app.target_row import filter_target_row_entities, find_target_row_keys, find_target_row_spans
from checkier.config import PipelineConfig
from checkier.entities import build_registry
from checkier.solver import solve_system


@pytest.mark.parametrize("style", ["numeric", "mnemonic"])
def test_scope_generator_emits_formulas_and_report_infers_exclusions(tmp_path, monkeypatch, style):
    tagged = {
        "1": ("data", '# <ent t="date">01.01.2025</ent>\n'
                      '<ent t="contract">TARGET</ent> '
                      '<ent t="date">01.01.2026</ent> <ent t="date">31.05.2026</ent>'),
        "2": ("data", '<ent t="contract">TARGET</ent> <ent t="contract">OTHER</ent> '
                      '<ent t="date">15.05.2026</ent>'),
        "3": ("data", '<ent t="contract">TARGET</ent> <ent t="date">03.06.2026</ent>'),
        "4": ("card", '<ent t="contract">OTHER</ent> <ent t="date">01.01.2025</ent>'),
        "5": ("data", 'No contract or parsed date: <ent t="date">unknown</ent>'),
        "6": ("data", '<ent t="contract">OTHER</ent>'),
    }
    registry = build_registry(tagged)
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path, id_style=style)
    mapping = {key: {"role": role, "original": key} for key, (role, _) in tagged.items()}
    mapping["6"]["duplicate_of"] = "2"
    report = Report(contract_number="TARGET", period_start=date(2026, 1, 1), period_end=date(2026, 5, 31))
    scope = {"target_row": None, "target_label": None}
    context = VerificationContext(cfg, {}, registry, scope, mapping, report)
    assert verification.GENERATORS["scope"] is generate_scope
    monkeypatch.setattr(verification, "GENERATORS", {"scope": generate_scope})

    requirements = verification.generate_requirements(context)
    assert len(requirements) == 8
    assert all(requirement["source"] == requirement["tag"] == "scope" for requirement in requirements)
    assert scope == {"target_row": None, "target_label": None}
    assert not (tmp_path / "scope.json").exists()
    definitions = [dict(requirement, id=i) for i, requirement in enumerate(requirements, 1)]
    result = verification.evaluate_requirements(context, definitions)
    failed = [relation for relation in result["relations"] if relation["status"] == "false"]
    assert [relation["files"] for relation in failed] == [["2"], ["3"]]
    assert result["files"]["out_of_scope"] == {"2": [failed[0]["equation"]], "3": [failed[1]["equation"]]}
    assert [entry["requirement_id"] for entry in result["requirement_results"]] == list(range(1, 9))

    # The scope tag, rather than the generator source, controls exclusion.
    for relation in failed:
        definitions[relation["index"]]["tag"] = "ref"
    assert verification.evaluate_requirements(context, definitions)["files"]["out_of_scope"] == {}

    # Removing all scope requirements also removes all inferred exclusions.
    monkeypatch.setattr(verification, "GENERATORS", {})
    assert verification.generate_requirements(context) == []
    assert verification.evaluate_requirements(context, [])["files"]["out_of_scope"] == {}


def test_scope_requires_whole_interval_containment(tmp_path):
    tagged = {"1": ("data", '<ent t="date">2 квартал 2026</ent>')}
    registry = build_registry(tagged)
    cfg = PipelineConfig(data_dir=tmp_path, out_dir=tmp_path, id_style="numeric")
    mapping = {"1": {"role": "data", "original": "quarter.pdf"}}
    scope = {"target_row": None, "target_label": None}
    for end, expected in ((date(2026, 5, 31), "false"), (date(2026, 6, 30), "true")):
        report = Report(period_start=date(2026, 4, 1), period_end=end)
        context = VerificationContext(cfg, {}, registry, scope, mapping, report)
        requirements = generate_scope(context)
        assert requirements[0]["equation"] == "01.04.2026 <= start(#0) && end(#0) <= " + end.strftime("%d.%m.%Y")
        relations, _, _, _ = solve_system(requirements, registry)
        assert relations[0].status == expected


def test_target_row_matches_full_label_through_entity_tags():
    tagged = {
        "card1": ("card", (
            '| 3 этап, 2025 г. | <ent t="amount">10</ent> |\n'
            '| 13 этап, 2026 г. | <ent t="amount">20</ent> |\n'
            '| <ent t="number">3</ent> этап, 2026 г. | <ent t="amount">30</ent> |\n'
        )),
        "data1": ("data", '| 3 этап, 2026 г. | <ent t="amount">40</ent> |\n'),
        "req1": ("request", 'Проверить строку «3 этап, 2025 г.»'),
    }
    registry = build_registry(tagged)
    keys = find_target_row_keys(tagged, registry, "3 этап, 2026 г.")
    assert keys == {3}


@pytest.mark.parametrize("label, row", [
    ("Итого", '<ent t="text">Итого</ent>'),
    ("3 этап, 2026 г.", '<ent t="number">3</ent> этап, <ent t="date">2026 г.</ent>'),
    ("3 этап, 2026 г.", '<ent t="text">3 этап, 2026 г.</ent>'),
    ("3 этап, 2026 г.", '<ent t="text">Строка 3 этап, 2026 г. отчёта</ent>'),
])
def test_target_row_excludes_entities_overlapping_label(label, row):
    tagged = {"card1": ("card", f'| {row} | <ent t="amount">3</ent> | <ent t="amount">2026</ent> |\n')}
    registry = build_registry(tagged)
    keys = find_target_row_keys(tagged, registry, label)
    assert keys == {entity.num for entity in registry.entities[-2:]}


def test_target_row_with_only_label_entities_selects_none():
    tagged = {"card1": ("card", '| <ent t="text">Итого</ent> | 10 |\n')}
    assert find_target_row_keys(tagged, build_registry(tagged), "Итого") == set()


@pytest.mark.parametrize("label", [
    "3 этап, 2025 г.",
    "13 этап, 2026 г.",
    "3 ЭТАП, 2026 г.",
    "3  этап, 2026 г.",
    "3 этап, 2026 гX",
    None,
    "",
])
def test_target_row_requires_literal_label(label):
    tagged = {"card1": ("card", '| 3 этап, 2026 г. | <ent t="amount">10</ent> |\n')}
    registry = build_registry(tagged)
    assert find_target_row_keys(tagged, registry, label) == (set() if label else None)


def test_target_row_does_not_match_part_of_an_id():
    tagged = {"card1": ("card", '| 13 этап, 2026 г. | <ent t="amount">10</ent> |\n')}
    assert find_target_row_keys(tagged, build_registry(tagged), "3 этап, 2026 г.") == set()


def test_target_row_ignores_matches_in_supporting_documents():
    tagged = {
        "card1": ("card", '| Другая строка | <ent t="amount">10</ent> |\n'),
        "data1": ("data", '| Итого | <ent t="amount">20</ent> |\n'),
    }
    assert find_target_row_keys(tagged, build_registry(tagged), "Итого") == set()


def test_target_row_with_no_entities_selects_none():
    tagged = {"card1": ("card", '| Итого | 10 |\n')}
    assert find_target_row_keys(tagged, build_registry(tagged), "Итого") == set()


def test_target_row_spans_work_with_ocr_text_before_entities_exist():
    text = '| 3 этап, 2025 г. | 10 |\r\n| 3 этап, 2026 г. | 20 |'
    start = text.index('| 3 этап, 2026 г.')
    assert find_target_row_spans(text, "3 этап, 2026 г.") == [(start, len(text))]
    assert find_target_row_spans(text, "3 этап, 2024 г.") == []


@pytest.mark.parametrize("label, markup", [
    ("Итого", '<ent t="text">Итого</ent>'),
    ("3 этап, 2026 г.", '<ent t="number">3</ent> этап, <ent t="date">2026 г.</ent>'),
    ("3 этап, 2026 г.", '<ent t="text">Строка 3 этап, 2026 г. отчёта</ent>'),
])
def test_filter_keeps_only_target_values_and_preserves_text(label, markup):
    amount = '<ent t="amount" u="RUB" n="1000" value="3000">3</ent>'
    text = (f'Договор <ent t="contract">ABC</ent>\r\n'
            f'| Другая строка | <ent t="amount">10</ent> |\r\n'
            f'| {markup} | {amount} |\r\n'
            f'| {markup} | {amount} |')
    plain_label = re.sub(r'</?ent\b[^>]*>', '', markup)
    filtered = filter_target_row_entities(text, label)
    assert filtered == (f'Договор ABC\r\n| Другая строка | 10 |\r\n'
                        f'| {plain_label} | {amount} |\r\n| {plain_label} | {amount} |')
    assert filter_target_row_entities(filtered, label) == filtered


@pytest.mark.parametrize("label", [None, ""])
def test_filter_without_target_keeps_all_entities(label):
    text = '| Итого | <ent t="amount" value="10">10</ent> |\n'
    assert filter_target_row_entities(text, label) == text


def test_filter_preserves_empty_target_selection():
    text = '| <ent t="text">Итого</ent> | 10 |\n| Другое | <ent t="amount">20</ent> |'
    assert filter_target_row_entities(text, "Итого") == '| Итого | 10 |\n| Другое | 20 |'
