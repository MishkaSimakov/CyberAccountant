"""Scope requirements use report settings; evaluated failures determine exclusions."""

from datetime import date

import pytest

from app import verification
from app.generators.scope import generate_scope
from app.models import Report
from app.target_row import find_target_row_keys, find_target_row_spans
from checkier.config import PipelineConfig
from checkier.entities import build_registry


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
    context = verification.VerificationContext(cfg, {}, registry, scope, mapping, report)
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
    assert keys == {2, 3}


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
    assert find_target_row_keys(tagged, registry, label) is None


def test_target_row_does_not_match_part_of_an_id():
    tagged = {"card1": ("card", '| 13 этап, 2026 г. | <ent t="amount">10</ent> |\n')}
    assert find_target_row_keys(tagged, build_registry(tagged), "3 этап, 2026 г.") is None


def test_target_row_ignores_matches_in_supporting_documents():
    tagged = {
        "card1": ("card", '| Другая строка | <ent t="amount">10</ent> |\n'),
        "data1": ("data", '| Итого | <ent t="amount">20</ent> |\n'),
    }
    assert find_target_row_keys(tagged, build_registry(tagged), "Итого") is None


def test_target_row_with_no_entities_returns_none():
    tagged = {"card1": ("card", '| Итого | 10 |\n')}
    assert find_target_row_keys(tagged, build_registry(tagged), "Итого") is None


def test_target_row_spans_work_with_ocr_text_before_entities_exist():
    text = '| 3 этап, 2025 г. | 10 |\r\n| 3 этап, 2026 г. | 20 |'
    start = text.index('| 3 этап, 2026 г.')
    assert find_target_row_spans(text, "3 этап, 2026 г.") == [(start, len(text))]
    assert find_target_row_spans(text, "3 этап, 2024 г.") == []
