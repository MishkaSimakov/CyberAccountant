"""Scope requirements use report settings; evaluated failures determine exclusions."""

from datetime import date

import pytest

from processing.verification.evaluation import SavedRequirement
from processing.verification.interface import VerificationInput
from processing.verification.processor import Verifier
from processing.common.requirements import FormulaDefinition
import logging
from dataclasses import asdict

from processing.common.corpus import ReviewedCorpus
from processing.common.documents import DocumentRevision, ReviewedDocument
from processing.common.scope import Scope
from processing.requirements.generators.scope import ScopeGenerator
from processing.requirements.interface import RequirementInput
from processing.requirements.options import RequirementOptions
from processing.requirements.processor import RequirementsProcessor
from app.models import Report
from processing.verification.target_row import find_target_row_keys, find_target_row_spans
from entity_fixtures import registry_from_drafts as build_registry


@pytest.mark.parametrize("style", ["numeric", "mnemonic"])
def test_scope_generator_emits_formulas_and_report_infers_exclusions(tmp_path, monkeypatch, style, llm_client):
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
    build_registry(tagged)
    report = Report(contract_number="TARGET", period_start=date(2026, 1, 1), period_end=date(2026, 5, 31))
    scope = {"target_row": None, "target_label": None}
    source = RequirementInput(ReviewedCorpus(1, tuple(
        ReviewedDocument(DocumentRevision(1, int(key), 1), key,
                         "final_report" if role == "card" else "supporting", markdown)
        for key, (role, markdown) in tagged.items() if key != "6"
    )), Scope(report.contract_number, report.period_start, report.period_end))
    processor = RequirementsProcessor(generators=[ScopeGenerator(RequirementOptions(id_style=style))])
    requirements = [asdict(definition) for definition in processor.process(
        source, logger=logging.getLogger(__name__)).definitions]
    assert len(requirements) == 8
    assert all(requirement["source"] == requirement["tag"] == "scope" for requirement in requirements)
    assert scope == {"target_row": None, "target_label": None}
    assert not (tmp_path / "scope.json").exists()
    definitions = [dict(requirement, id=i) for i, requirement in enumerate(requirements, 1)]

    def verify(definitions):
        saved = tuple(SavedRequirement(item["id"], FormulaDefinition(
            equation=item["equation"], tag=item["tag"], source=item["source"])) for item in definitions)
        return Verifier().process(VerificationInput(source.corpus, source.scope, saved),
                                  logger=logging.getLogger(__name__)).report

    result = verify(definitions)
    failed = [relation for relation in result["relations"] if relation["status"] == "false"]
    assert [relation["files"] for relation in failed] == [["2"], ["3"]]
    assert result["files"]["out_of_scope"] == {"2": [failed[0]["equation"]], "3": [failed[1]["equation"]]}
    assert [entry["requirement_id"] for entry in result["requirement_results"]] == list(range(1, 9))

    # The scope tag, rather than the generator source, controls exclusion.
    for relation in failed:
        definitions[relation["index"]]["tag"] = "ref"
    assert verify(definitions)["files"]["out_of_scope"] == {}

    # Removing all scope requirements also removes all inferred exclusions.
    assert RequirementsProcessor(generators=[]).process(source, logger=logging.getLogger(__name__)).definitions == ()
    assert verify([])["files"]["out_of_scope"] == {}


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
